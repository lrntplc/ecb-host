#!/usr/bin/env python

import chess
import chess.uci
from threading import Timer


class EcbFSM(object):
    EVENT = {
        "SENSORS_CHANGED":      0,
        "CLOCK_EXPIRED":        1,
        "BUTTON_PRESSED":       2,
        "HUMAN_STARTED":        3,
        "HUMAN_STOPPED":        3,
        "ENGINE_STARTED":       4,
        "ENGINE_STOPPED":       5,
        "ENGINE_MOVED":         6,
    }
    MODE = {
        "NORMAL": 0,
        "LEARN":  1
    }
    COLOR = {
        "BLACK": 0,
        "WHITE": 1,
    }
    LEVEL = {
        "DISABLED": 0,  # opponent is human
        "EASY":     1,  #
        "MEDIUM":   2,  # opponent is Edison
        "HARD":     3,  #
    }

    LEVEL_LEDS_MAP = [
        [0, 0, 0],  # LEVEL DISABLED
        [1, 0, 0],  # LEVEL EASY
        [0, 1, 0],  # LEVEL MEDIUM
        [0, 0, 1],  # LEVEL HARD
    ]

    DEFAULT_CONFIG = {
        "mode":           MODE["NORMAL"],
        "level":          LEVEL["EASY"],
        "engine_color":   COLOR["WHITE"],
        "time": {
            "min": 0,
            "sec": 0
        }
    }

    def __init__(self, path_to_engine, ecb_driver):
        self.path_to_engine = path_to_engine
        self.current_state = self._state_idle
        self.driver = ecb_driver
        self.game_time = {
            'min': 0,
            'sec': 0,
        }

        self.engine = None
        self.board = None

        self.game_config = self.DEFAULT_CONFIG
        self.game_state = {
            'wtime': 0,
            'btime': 0,
            'current_clock': self.driver.CLOCK_BOTTOM,
            'current_move': {},
            'move_turn': self.COLOR['WHITE'],
            'moves': [],
        }

        self.driver.set_callbacks(self._sensors_callback,
                                  self._clock_expired_callback,
                                  self._cmd_callback)

        self.driver.clock_blank(self.driver.CLOCK_BOTTOM)
        self.driver.clock_blank(self.driver.CLOCK_TOP)
        self._cmd_panel_leds_update()

    def _sensors_callback(self, changed_squares):
        print("sensors callback: " + changed_squares)
        self.current_state({
            'type': self.EVENT['SENSORS_CHANGED'],
            'data': changed_squares
        })

    def _clock_expired_callback(self, clock_id):
        print("clock expired: " + clock_id)
        self.current_state({
            'type': self.EVENT['CLOCK_EXPIRED'],
            'data': clock_id
        })

    def _cmd_callback(self, buttons_mask):
        self.current_state({
            'type': self.EVENT['BUTTON_PRESSED'],
            'data': buttons_mask,
        })

    def _cmd_panel_leds_update(self):
        mode_cmd = [self.driver.btn_led_off, self.driver.btn_led_on][self.game_config['mode']]
        level_led_map = self.LEVEL_LEDS_MAP[self.game_config['level']]
        color_cmd = [self.driver.btn_led_off, self.driver.btn_led_on][self.game_config['engine_color']]

        mode_cmd(self.driver.CMD_LED_MODE)
        color_cmd(self.driver.CMD_LED_OPP_COLOR)

        for i in range(0, 3):
            level_cmd = [self.driver.btn_led_off, self.driver.btn_led_on][level_led_map[i]]
            level_cmd(1 << (i + 2))

    def _bits_in_byte(self, byte):
        half_byte_map = [0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4]

        low_byte_bits = half_byte_map[byte & 0xf]
        high_byte_bits = half_byte_map[(byte & 0xf0) >> 4]

        return low_byte_bits + high_byte_bits

    def _get_chessmen_no(self, sensor_map):
        chessmen_no = 0
        for row in range(0, 8):
            chessmen_no += self._bits_in_byte(sensor_map[row])

        return chessmen_no

    def _is_new_game(self, sensor_map):
        return sensor_map[0] == 0xff and sensor_map[1] == 0xff and\
            sensor_map[6] == 0xff and sensor_map[7] == 0xff

    def _is_new_game_missing_pieces(self, sensor_map):
        chessmen_no = self._get_chessmen_no(sensor_map)

        return chessmen_no > 25 and not sensor_map[2] and not sensor_map[3] and\
            not sensor_map[4] and not sensor_map[5]

    def _get_initial_pos_missing_squares(self, sensor_map):
        initial_pos_rows = [0, 1, 6, 7]
        missing_squares = []

        for i in range(0, 4):
            row_pos = initial_pos_rows[i]
            row_val = ~(sensor_map[initial_pos_rows[i]])

            missing_squares += self._row_to_squares(row_pos, row_val)

        return missing_squares

    def _get_initial_position(self, sensor_map):
        if self._is_new_game(sensor_map):
            self.driver._leds_blink()  # stop blinking leds, if any
            return chess.STARTING_FEN
        elif self._is_new_game_missing_pieces(sensor_map):
            missing_squares = self._get_initial_pos_missing_squares(sensor_map)

            self.driver.leds_blink(missing_squares)
        else:
            # TODO
            print("Further setup needed to set position.")

        return None

    def _game_start(self):
        def on_uci_done():
            self.current_state({'type': self.EVENT['ENGINE_STARTED'],
                                'data': None})

        self.current_state = self._state_starting_game

        if self.game_config['level'] != self.LEVEL['DISABLED']:
            print("starting chess engine")

            self.engine = chess.uci.popen_engine(self.path_to_engine)
            self.engine.uci()
            self.engine.ucinewgame(async_callback=on_uci_done)
        else:
            print("playing against human")
            self.current_state({'type': self.EVENT['HUMAN_STARTED'],
                                'data': None})

    def _game_stop(self):
        def on_quit_done():
            self.current_state({'type': self.EVENT['ENGINE_STOPPED'],
                                'data': None})

        self.current_state = self._state_stopping_game

        if self.game_config['level'] != self.LEVEL['DISABLED']:
            self.engine.quit(on_quit_done)
        else:
            self.current_state({'type': self.EVENT['HUMAN_STOPPED'],
                                'data': None})

    def _get_legal_moves(self, square):
        legal_moves = []

        for move in self.board.legal_moves:
            if square == chess.SQUARE_NAMES[move.from_square]:
                legal_moves.append(chess.SQUARE_NAMES[move.to_square])

        return legal_moves

    def _chess_engine_position_analyze(self):
        def on_go_finished(command):
            self.current_state({'type': self.EVENT['CHESS_ENGINE_MOVED'],
                                'data': command.result()})

        wtime_msec = (self.game_state['wtime']['min'] * 60 +
                      self.game_state['wtime']['sec']) * 1000
        btime_msec = (self.game_state['btime']['min'] * 60 +
                      self.game_state['btime']['sec']) * 1000

        self.engine.go(wtime=wtime_msec,
                       btime=btime_msec,
                       async_callback=on_go_finished)

    # FSM states
    def _state_idle(self, ecb_event):
        print("idle state: " + str(ecb_event))

        if ecb_event['type'] == self.EVENT['SENSORS_CHANGED']:
            print(ecb_event['data'])

        if ecb_event['type'] == self.EVENT['BUTTON_PRESSED']:
            if ecb_event['data'] & self.driver.CMD_BTN_MODE:
                self.game_config['mode'] ^= 1

            if ecb_event['data'] & self.driver.CMD_BTN_OPP_LEVEL:
                self.game_config['level'] = (self.game_config['level'] + 1) & 3

            if ecb_event['data'] & self.driver.CMD_BTN_OPP_COLOR:
                self.game_config['engine_color'] ^= 1

            if ecb_event['data'] & self.driver.CMD_BTN_GAME_TIME:
                if self.game_config['time']['min'] == 0:
                    self.game_config['time']['min'] = 90
                else:
                    self.game_config['time']['min'] -= 10

                if self.game_config['time']['min']:
                    minutes = self.game_config['time']['min']
                    sec = self.game_config['time']['sec']

                    self.driver.clock_set(self.driver.CLOCK_BOTTOM, minutes, sec)
                    self.driver.clock_set(self.driver.CLOCK_TOP, minutes, sec)
                else:
                    self.driver.clock_blank(self.driver.CLOCK_BOTTOM)
                    self.driver.clock_blank(self.driver.CLOCK_TOP)

            if ecb_event['data'] & self.driver.CMD_BTN_GAME_START:
                self.driver.sensors_start()

                self.game_state['wtime'] = self.game_config['time']
                self.game_state['btime'] = self.game_config['time']
                self.game_state['move_turn'] = self.COLOR['WHITE']
                self.game_state['current_clock'] = self.driver.CLOCK_BOTTOM
                self.game_state['moves'] = []

                Timer(1, self._game_start)

        self._cmd_panel_leds_update()

    def _state_starting_game(self, ecb_event):
        print("starting game state: " + str(ecb_event))

        self.driver.btn_led_on(self.driver.CMD_LED_START)
        sensor_map = self.driver.sensors_get()

        if ecb_event['type'] == self.EVENT['BUTTON_PRESSED'] and \
                (ecb_event['data'] & self.driver.CMD_BTN_GAME_START):
            self._game_stop()

        if ecb_event['type'] == self.EVENT['SENSORS_CHANGED'] or\
                ecb_event['type'] == self.EVENT['ENGINE_STARTED'] or\
                ecb_event['type'] == self.EVENT['HUMAN_STARTED']:

            chess_position = self._get_initial_position(sensor_map)
            if chess_position is not None:
                self.board = chess.Board(chess_position)

                if self.game_config['level'] != self.LEVEL['DISABLED'] and\
                        self.game_state['move_turn'] == self.game_config['engine_color']:
                    self.engine.position(self.board)
                    self._chess_engine_position_analyze()

                self.driver.clock_start(self.game_state['current_clock'])

                self.current_state = self._state_game

    def _state_stopping_game(self, ecb_event):
        print("stopping game state: " + str(ecb_event))
        if ecb_event['type'] in [self.EVENT['ENGINE_STOPPED'], self.EVENT['HUMAN_STOPPED']]:
            self.driver.sensors_stop()
            self.driver.btn_led_off(self.driver.CMD_LED_START)
            self.driver.leds_blink()
            self.driver.clock_stop(self.driver.CLOCK_BOTTOM)
            self.driver.clock_stop(self.driver.CLOCK_TOP)
            self.current_state = self._state_idle

    def _state_game(self, ecb_event):
        print("game state: " + str(ecb_event))
        if ecb_event['type'] == self.EVENT['SENSORS_CHANGED']:
            if len(ecb_event['data']) == 1:
                self.game_state['current_move']['from'] = ecb_event['data'][0]

                self.driver.leds_on(self._get_legal_moves([ecb_event['data'][0]]))

                if not self.game_state['legal_moves']:
                    return

                self.driver.leds_blink(self.ecb_event['data'])

                self.current_state = self._state_move

        if ecb_event['type'] == self.EVENT['BUTTON_PRESSED'] and \
                (ecb_event['data'] & self.driver.CMD_BTN_GAME_START):
            self._game_stop()

        if ecb_event['type'] == self.EVENT['CHESS_ENGINE_MOVED']:
            self._toggle_clock()
            self.driver.leds_blink([ecb_event['data']['from']],
                                   [ecb_event['data']['to']])
            self.game_state['move_turn'] ^= 1
            self.board.push_san(ecb_event['data']['from'] +
                                ecb_event['data']['to'])
            self.game_state['current_move'] = ecb_event['data']

            self.current_state = self._state_engine_move


