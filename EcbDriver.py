#!/usr/bin/env python

import mraa
from threading import Timer


def isr_cb(parent_obj):
    parent_obj._isr_handler()


class Controller(mraa.I2c):
    def __init__(self, i2c_addr, int_pin, cb=None):
        super(Controller, self).__init__(6)

        self.cb = cb

        self.address(i2c_addr)
        self.frequency(0)

        self.int_pin = mraa.Gpio(int_pin)
        self.int_pin.dir(mraa.DIR_IN)
        self.int_pin.isr(mraa.EDGE_FALLING, isr_cb, self)

        self._ctrlr_init()

    def _ctrlr_init(self):
        pass

    def _isr_handler(self):
        pass

    def reg_bit_set(self, reg, bit):
        old_val = self.readReg(reg)
        new_val = old_val | bit
        self.writeReg(reg, new_val)

    def reg_bit_clear(self, reg, bit):
        old_val = self.readReg(reg)
        new_val = old_val & ~bit
        self.writeReg(reg, new_val)


class HbController(Controller):
    REGS = {
        'led_row_0':    0,
        'led_row_1':    1,
        'led_row_2':    2,
        'led_row_3':    3,
        'sensor_row_0': 4,
        'sensor_row_1': 5,
        'sensor_row_2': 6,
        'sensor_row_3': 7,
        'clock_min':    8,
        'clock_sec':    9,
        'command':      10,
        'status':       11
    }

    CMD_CLOCK_START = 1 << 0
    CMD_SENSORS_START = 1 << 1
    CMD_CLOCK_BLANK = 1 << 2

    STATUS_CLOCK_EXPIRED = 1 << 0
    STATUS_SENSORS_CHANGED = 1 << 1

    def _ctrlr_init(self):
        # switch off clock and sensors scanning
        self.writeReg(self.REGS['command'], 0)

        # switch off all leds
        self.write(bytearray([self.REGS['led_row_0'], 0, 0, 0, 0]))

        # read sensor rows status, just to ack them
        self.readBytesReg(self.REGS['sensor_row_0'], 4)

        # clear the interrupt, if any
        status_val = self.readReg(self.REGS['status'])
        self.writeReg(self.REGS['status'], status_val)

        self.sensor_map = [0, 0, 0, 0]
        self.led_map = [0, 0, 0, 0]

    def leds_switch(self, led_map):
        self.write(bytearray([self.REGS['led_row_0']] + led_map))

    def sensor_scan_switch(self, on):
        reg_change = [self.reg_bit_clear, self.reg_bit_set][on]

        reg_change(self.REGS['command'], self.CMD_SENSORS_START)

    def clock_set(self, min, sec):
        self.write(bytearray([self.REGS['clock_min'], min, sec]))

    def clock_get(self):
        data = self.readBytesReg(self.REGS['clock_min'], 2)

        return (data[0], data[1])

    def clock_switch(self, on):
        reg_change = [self.reg_bit_clear, self.reg_bit_set][on]

        reg_change(self.REGS['command'], self.CMD_CLOCK_START)

    def clock_blank(self):
        self.reg_bit_set(self.REGS['command'], self.CMD_CLOCK_BLANK)

    # returns a bytearray
    def sensors_read(self):
        return self.readBytesReg(self.REGS['sensor_row_0'], 4)

    # returns a bytearray
    def leds_read(self):
        return self.readBytesReg(self.REGS['leds_row_0'], 4)

    def _isr_handler(self):
        status = self.readReg(self.REGS['status'])
        self.writeReg(self.REGS['status'], status)

        if status:
            self.cb((status & self.STATUS_SENSORS_CHANGED) != 0,
                    (status & self.STATUS_CLOCK_EXPIRED) != 0)


class CmdController(Controller):
    REGS = {
        'leds': 0,
        'buttons': 1
    }

    def _ctrlr_init(self):
        # switch off all command panel leds
        self.writeReg(self.REGS['leds'], 0)

        # reset button states
        btns_state = self.readReg(self.REGS['buttons'])
        self.writeReg(self.REGS['buttons'], btns_state)

    def _isr_handler(self):
        btns = self.readReg(self.REGS['buttons'])
        self.writeReg(self.REGS['buttons'], btns)

        self.cb(btns)

    def leds_switch(self, led_mask, on):
        reg_cmd = [self.reg_bit_clear, self.reg_bit_set][on]

        reg_cmd(self.REGS['leds'], led_mask)


class EcbDriver(object):
    SENSOR_COLUMNS = "abcdefgh"

    # my leds are wired in reverse order... :/
    LED_COLUMNS = "hgfedcba"

    CLOCK_BOTTOM = 0
    CLOCK_TOP = 1

    # command panel button IDs
    CMD_BTN_MODE = 1 << 0
    CMD_BTN_OPP_LEVEL = 1 << 1
    CMD_BTN_OPP_COLOR = 1 << 2
    CMD_BTN_GAME_TIME = 1 << 3
    CMD_BTN_GAME_START = 1 << 4

    # command panel led IDs
    CMD_LED_START = 1 << 0
    CMD_LED_OPP_COLOR = 1 << 1
    CMD_LED_OPP_LEVEL0 = 1 << 2
    CMD_LED_OPP_LEVEL1 = 1 << 3
    CMD_LED_OPP_LEVEL2 = 1 << 4
    CMD_LED_MODE = 1 << 5
    CMD_LED_WIFI_ON = 1 << 6
    CMD_LED_BT_ON = 1 << 7

    def __init__(self):
        self.sensors_changed_cb = None
        self.clock_expired_cb = None
        self.btn_pressed_cb = None

        self.led_map = [0, 0, 0, 0, 0, 0, 0, 0]
        self.sensor_map = [0, 0, 0, 0, 0, 0, 0, 0]

        self.blink_onoff_map = [0, 0, 0, 0, 0, 0, 0, 0]
        self.blink_offon_map = [0, 0, 0, 0, 0, 0, 0, 0]
        self.blink_state = 0
        self.blink_interval = None

        self.top = HbController(0x11, 31, self._top_int_cb)
        self.bot = HbController(0x12, 32, self._bot_int_cb)
        self.cmd = CmdController(0x13, 33, self._cmd_int_cb)

    def _set_interval(self, timer_function, timeout):
        def interval_wrapper():
            timer_function()
            self._set_interval(timer_function, timeout)

        self.blink_interval = Timer(timeout, interval_wrapper)
        self.blink_interval.start()

    def _clear_interval(self):
        if self.blink_interval is not None:
            self.blink_interval.cancel()
            self.blink_interval = None

    def _handle_sensor_changes(self, ctrl):
        def rows_to_squares(rows_list, ctrl):
            sq_list = []
            row_offs = [0, 4][ctrl == self.top]

            for row in range(0, 4):
                for col in range(0, 8):
                    if rows_list[row] & (1 << col):
                        sq_list += ["%s%d" % (self.SENSOR_COLUMNS[col],
                                              row + row_offs + 1)]

            return sq_list

        row_offs = [0, 4][ctrl == self.top]
        old_sensor_map = self.sensor_map[row_offs: row_offs + 4]

        buf = ctrl.sensors_read()
        changed_sensor_map = [0, 0, 0, 0]

        for row in range(0, 4):
            changed_sensor_map[row] = old_sensor_map[row] ^ buf[row]
            self.sensor_map[row + row_offs] = buf[row]

        if self.sensors_changed_cb is not None:
            self.sensors_changed_cb(rows_to_squares(changed_sensor_map, ctrl))

    def _top_int_cb(self, sensors_changed, clock_expired):
        if clock_expired:
            if self.clock_expired_cb is not None:
                self.clock_expired_cb(self.CLOCK_TOP)
        else:
            self._handle_sensor_changes(self.top)

    def _bot_int_cb(self, sensors_changed, clock_expired):
        if clock_expired:
            if self.clock_expired_cb is not None:
                self.clock_expired_cb(self.CLOCK_BOTTOM)
        else:
            self._handle_sensor_changes(self.bot)

    def _cmd_int_cb(self, btns):
        if self.btns_pressed_cb is not None:
            self.btns_pressed_cb(btns)

    def _squares_to_map(self, squares_list):
        rows_map = [0, 0, 0, 0, 0, 0, 0, 0]

        for sq in squares_list:
            c = self.LED_COLUMNS.index(sq[0])
            r = int(sq[1]) - 1

            rows_map[r] |= 1 << c

        return rows_map

    def _leds_switch(self, new_led_map):
        self.led_map = new_led_map

        self.bot.leds_switch(new_led_map[0:4])
        self.top.leds_switch(new_led_map[4:8])

    def _leds_on(self, on_led_map):
        new_led_map = self.led_map

        for row in range(len(on_led_map)):
            new_led_map[row] |= on_led_map[row]

        self._leds_switch(new_led_map)

    def _leds_off(self, off_led_map):
        new_led_map = self.led_map

        for row in range(len(off_led_map)):
            new_led_map[row] &= ~off_led_map[row]

        self._leds_switch(new_led_map)

    def _leds_blink(self):
        new_map = self.led_map

        for row in range(0, 8):
            if self.blink_state:
                new_map[row] |= self.blink_onoff_map[row]
                new_map[row] &= ~self.blink_offon_map[row]
            else:
                new_map[row] &= ~self.blink_onoff_map[row]
                new_map[row] |= self.blink_offon_map[row]

        self.blink_state ^= 1
        self._leds_switch(new_map)

    # set callbacks
    def set_callbacks(self, sensors_changed_cb, clock_expired_cb, btns_pressed_cb):
        self.sensors_changed_cb = sensors_changed_cb
        self.clock_expired_cb = clock_expired_cb
        self.btns_pressed_cb = btns_pressed_cb

    # LEDs API
    def leds_on(self, squares_list):
        led_map = self._squares_to_map(squares_list)

        self._leds_on(led_map)

    def leds_off(self, squares_list):
        led_map = self._squares_to_map(squares_list)

        self._leds_off(led_map)

    def leds_blink(self, onoff_squares=None, offon_squares=None):
        def switch_off_leftovers(old_map, new_map):
            leftover_map = [0]*8

            for row in range(0, 8):
                leftover_map[row] = old_map[row] ^ new_map[row]

            self._leds_off(leftover_map)

        if onoff_squares is None:
            self._leds_off(self.blink_onoff_map)
            self.blink_onoff_map = [0]*8
        else:
            new_map = self._squares_to_map(onoff_squares)
            switch_off_leftovers(self.blink_onoff_map, new_map)

            self.blink_onoff_map = new_map

        if offon_squares is None:
            self._leds_off(self.blink_offon_map)
            self.blink_offon_map = [0]*8
        else:
            new_map = self._squares_to_map(offon_squares)
            switch_off_leftovers(self.blink_offon_map, new_map)

            self.blink_offon_map = new_map

        if onoff_squares is None and offon_squares is None:
            if self.blink_interval is not None:
                self._clear_interval()
                self.blink_interval = None

            return

        # we're already blinking
        if self.blink_interval is not None:
            return

        self.blink_state = 1
        self.blink_interval = self._set_interval(self._leds_blink, 0.5)

    # Sensors API
    def sensors_start(self):
        self.top.sensor_scan_switch(1)
        self.bot.sensor_scan_switch(1)

    def sensors_stop(self):
        self.top.sensor_scan_switch(0)
        self.bot.sensor_scan_switch(0)

    def sensors_get(self):
        return self.sensor_map

    # Clock API
    def clock_set(self, clock_id, min, sec):
        ctrl = [self.bot, self.top][clock_id]

        ctrl.clock_set(min, sec)

    def clock_get(self, clock_id):
        ctrl = [self.bot, self.top][clock_id]

        ctrl.clock_get()

    def clock_start(self, clock_id):
        ctrl = [self.bot, self.top][clock_id]

        ctrl.clock_switch(1)

    def clock_stop(self, clock_id):
        ctrl = [self.bot, self.top][clock_id]

        ctrl.clock_switch(0)

    def clock_blank(self, clock_id):
        ctrl = [self.bot, self.top][clock_id]

        ctrl.clock_blank()

    # Command panel API
    def btn_led_on(self, led_mask):
        self.cmd.leds_switch(led_mask, 1)

    def btn_led_off(self, led_mask):
        self.cmd.leds_switch(led_mask, 0)


if __name__ == "__main__":
    def sensors_changed_cb(sq_list):
        print("sensors changed cb called: %s" % str(sq_list))

    def clock_expired_cb(clock_id):
        print("clock expired cb called: %d" % clock_id)

    def btns_pressed_cb(btns):
        print("buttons pressed called: %d" % btns)

    import time
    driver = EcbDriver()
    driver.set_callbacks(sensors_changed_cb, clock_expired_cb, btns_pressed_cb)
    driver.sensors_start()
    for min, sec in [(90, 0), (80, 0), (70, 0), (60, 0)]:
        driver.clock_set(driver.CLOCK_BOTTOM, min, sec)
        driver.clock_set(driver.CLOCK_TOP, min, sec)
        time.sleep(1)
    driver.clock_start(driver.CLOCK_BOTTOM)
    driver.clock_start(driver.CLOCK_TOP)
    driver.leds_blink(['a2', 'a4'], ['h2', 'h8'])
    time.sleep(10)
    driver.leds_blink()
    driver.clock_stop(driver.CLOCK_BOTTOM)
    driver.clock_stop(driver.CLOCK_TOP)
    time.sleep(3)
    driver.clock_blank(driver.CLOCK_BOTTOM)
    driver.clock_blank(driver.CLOCK_TOP)
    driver.sensors_stop()
