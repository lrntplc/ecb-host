#!/usr/bin/env python

from EcbDriver import EcbDriver
import chess
import chess.uci
import Queue
from threading import Timer


class State(object):
    def run(self, ecb, event, event_data=None):
        assert 0, "not implemented"

    def next(self, event):
        assert 0, "not implemented"


class Event(object):
    def __init__(self, event_description):
        self.event_description = event_description

    def __str__(self):
        return self.event_description

Event.sensors_changed = Event("sensors changed")
Event.clock_expired = Event("clock expired")
Event.game_config = Event("one of the config buttons was pressed")
Event.game_start = Event("start game button was pressed")
Event.game_started = Event("game started")
Event.game_stopped = Event("game stopped")
Event.move_started = Event("move started")
Event.move_ended = Event("move ended")
Event.move_aborted = Event("move aborted")
Event.engine_move_started = Event("engine indicated a move")
Event.engine_move_ended = Event("engine piece was moved")


class StateMachine(object):
    def __init__(self, initial_state):
        self.current_state = initial_state

    def handle(self, ecb, event, event_data):
        self.current_state = self.current_state.next(event)
        self.current_state.run(ecb, event, event_data)


class Idle(State):
    def run(self, ecb, event, event_data):
        print("idle: " + str(event))

    def next(self, event):
        if event == Event.game_config:
            return Ecb.setup
        if event == Event.game_start:
            return Ecb.starting

        return Ecb.idle


class Setup(State):
    def run(self, ecb, event, event_data):
        print("setup: " + str(event))
        if event != Event.game_config:
            return

        if event_data & EcbDriver.CMD_BTN_MODE:
            ecb.game_config.mode_change()
        if event_data & EcbDriver.CMD_BTN_OPP_LEVEL:
            ecb.game_config.level_change()
        if event_data & EcbDriver.CMD_BTN_OPP_COLOR:
            ecb.game_config.opp_color_change()
        if event_data & EcbDriver.CMD_BTN_GAME_TIME:
            ecb.game_config.time_change()

        ecb.game_config.update(ecb.driver)

    def next(self, event):
        if event == Event.game_start:
            return Ecb.starting

        return Ecb.setup


class Starting(State):
    POSITION_NEW = 0
    POSITION_CUSTOM = 1

    def _row_to_squares(self, row, val):
        columns = "abcdefgh"
        squares = []

        for i in range(0, 8):
            if (val & (1 << i)):
                squares.append("%s%d" % (columns[i], row + 1))

        return squares

    def _bits_in_byte(self, byte):
        half_byte_map = [0, 1, 1, 2, 1, 2, 2, 3, 1, 2, 2, 3, 2, 3, 3, 4]

        low_byte_bits = half_byte_map[byte & 0xf]
        high_byte_bits = half_byte_map[(byte & 0xf0) >> 4]

        return low_byte_bits + high_byte_bits

    def _get_chessmen_no(self, sensors_map):
        chessmen_no = 0
        for row in range(0, 8):
            chessmen_no += self._bits_in_byte(sensors_map[row])

        return chessmen_no

    def _detect_position_type(self, sensors_map):
        chessmen_no = self._get_chessmen_no(sensors_map)
        print("detected %d chessmen" % chessmen_no)

        if chessmen_no > 25 and not sensors_map[2] and not sensors_map[3] and \
                not sensors_map[4] and not sensors_map[5]:
            return self.POSITION_NEW

        return self.POSITION_CUSTOM

    def _new_game_unknown_squares(self, sensors_map):
        initial_pos_rows = [0, 1, 6, 7]
        unknown_squares = []

        for i in range(0, 4):
            row_pos = initial_pos_rows[i]
            row_val = ~(sensors_map[initial_pos_rows[i]])

            unknown_squares += self._row_to_squares(row_pos, row_val)

        return unknown_squares

    def _attempt_start(self, ecb):
        sensors_map = ecb.driver.sensors_get()
        print(str(sensors_map))
        position_type = self._detect_position_type(sensors_map)

        if position_type == self.POSITION_NEW:
            unknown_squares = self._new_game_unknown_squares(sensors_map)
            if len(unknown_squares):
                ecb.driver.leds_blink(unknown_squares)
                return

            ecb.board = chess.Board(chess.STARTING_FEN)
        else:
            print("TODO: custom position")
            return

        ecb.driver.leds_blink()

        if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            print("Play against engine. Starting engine...")
            ecb.engine = chess.uci.popen_engine(ecb.path_to_engine)
            ecb.engine.uci()
            ecb.engine.ucinewgame()
        else:
            print("Play against human.")

        ecb.event_queue.put((Event.game_started, None))

    def run(self, ecb, event, event_data):
        print("starting: " + str(event))
        if event == Event.game_start and not ecb.driver.sensors_running():
            ecb.game_config.update(ecb.driver)
            ecb.driver.sensors_start()

            # we need a small delay for the sensors to settle
            Timer(1, self._attempt_start, [ecb]).start()

        if event == Event.sensors_changed:
            self._attempt_start(ecb)

    def next(self, event):
        if event == Event.game_started:
            return Ecb.game
        elif event == Event.game_start:
            return Ecb.stopping

        return Ecb.starting


class Stopping(State):
    def run(self, ecb, event, event_data):
        print("stopping: " + str(event))
        if event == Event.game_start:
            ecb.driver.sensors_stop()
            ecb.driver.btn_led_off(EcbDriver.CMD_LED_START)
            ecb.driver.leds_blink()
            ecb.driver.clock_stop(EcbDriver.CLOCK_BOTTOM)
            ecb.driver.clock_stop(EcbDriver.CLOCK_TOP)
            ecb.driver.clock_blank(EcbDriver.CLOCK_BOTTOM)
            ecb.driver.clock_blank(EcbDriver.CLOCK_TOP)

            if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                ecb.engine.quit()

            ecb.event_queue.put((Event.game_stopped, None))

    def next(self, event):
        if event == Event.game_stopped:
            return Ecb.idle

        return Ecb.stopping


class Game(State):
    def _get_legal_moves(self, board, square):
        legal_moves = []

        for move in board.legal_moves:
            if square == chess.SQUARE_NAMES[move.from_square]:
                legal_moves.append(chess.SQUARE_NAMES[move.to_square])

        return legal_moves

    def _engine_go(self, ecb):
        def engine_on_go_finished(command):
            ecb.event_queue.put((Event.engine_move_started, command))

        wtime = self.time[chess.WHITE]
        btime = self.time[chess.BLACK]
        wtime_msec = (wtime['min'] * 60 + wtime['sec']) * 1000
        btime_msec = (btime['min'] * 60 + btime['sec']) * 1000

        ecb.engine.position(ecb.board)
        ecb.engine.go(wtime=wtime_msec, btime=btime_msec,
                      async_callback=engine_on_go_finished)

    def run(self, ecb, event, event_data):
        print("game: " + str(event))

        if event == Event.game_started:
            self.time = [ecb.game_config.time, ecb.game_config.time]

            if ecb.board.turn == ecb.game_config.opp_color and\
                    ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                self._engine_go(ecb)

            ecb.driver.clock_start(ecb.board.turn)
            ecb.driver.btn_led_on(EcbDriver.CMD_LED_START)

        if event == Event.sensors_changed:
            if len(event_data) != 1:
                raise Exception("TODO: More than one piece has changed")

            # ignore events from engine pieces
            if ecb.board.turn == ecb.game_config.opp_color and \
                    ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                return

            self.from_sq = chess.SQUARE_NAMES.index(event_data[0])
            legal_moves = self._get_legal_moves(ecb.board, event_data[0])

            if len(legal_moves) == 0:
                return

            move_started_ev_data = {
                'from': event_data,
                'legal_moves': legal_moves
            }
            ecb.event_queue.put((Event.move_started, move_started_ev_data))

        if event == Event.move_ended:
            to_sq = chess.SQUARE_NAMES.index(event_data[0])

            move = chess.Move(from_square=self.from_sq, to_square=to_sq)

            ecb.driver.clock_stop(ecb.board.turn)
            self.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)
            ecb.board.push(move)
            if ecb.board.turn == ecb.game_config.opp_color and \
                    ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                self._engine_go(ecb)

            ecb.driver.clock_start(ecb.board.turn)

        if event == Event.game_config:
            if not event_data & EcbDriver.CMD_BTN_MODE:
                return

            ecb.game_config.mode_change()
            ecb.game_config.update_leds(ecb.driver)

    def next(self, event):
        if event == Event.game_start:
            return Ecb.stopping
        elif event == Event.move_started:
            return Ecb.move
        elif event == Event.engine_move_started:
            return Ecb.engine_move

        return Ecb.game


class Move(State):
    def run(self, ecb, event, event_data):
        if event == Event.move_started:
            self.move_start = event_data

            ecb.driver.leds_blink(event_data['from'])

            if ecb.game_config.mode == GameConfig.MODE_LEARN:
                ecb.driver.leds_on(event_data['legal_moves'])

        if event == Event.sensors_changed:
            if len(event_data) != 1:
                raise Exception("TODO: More than one piece has changed")

            if event_data != self.move_start['from'] and\
                    event_data[0] not in self.move_start['legal_moves']:
                return

            if self.move_start['from'] == event_data:
                ecb.event_queue.put((Event.move_aborted, None))

            if event_data[0] in self.move_start['legal_moves']:
                ecb.event_queue.put((Event.move_ended, event_data))

            if ecb.game_config.mode == GameConfig.MODE_LEARN:
                ecb.driver.leds_off(self.move_start['legal_moves'])

            ecb.driver.leds_blink()

    def next(self, event):
        if event == Event.move_ended:
            return Ecb.game
        elif event == Event.move_aborted:
            return Ecb.game
        elif event == Event.game_start:
            return Ecb.stopping

        return Ecb.move


class EngineMove(State):
    def run(self, ecb, event, event_data):
        if event == Event.engine_move_started:
            bestmove, ponder = event_data.result()
            self.from_sq = chess.SQUARE_NAMES[bestmove.from_square]
            self.to_sq = chess.SQUARE_NAMES[bestmove.to_square]

            ecb.driver.leds_blink([self.from_sq], [self.to_sq])
            ecb.driver.clock_stop(ecb.board.turn)
            ecb.board.push(bestmove)
            ecb.driver.clock_start(ecb.board.turn)

        if event == Event.sensors_changed:
            if len(event_data) != 1:
                raise Exception('TODO: handle multiple pieces moved')

            if event_data[0] == self.from_sq:
                ecb.driver.leds_blink(None, [self.to_sq])
                self.from_sq = ''
            elif event_data[0] == self.to_sq and self.from_sq == '':
                ecb.driver.leds_blink()

                ecb.event_queue.put((Event.engine_move_ended, None))

    def next(self, event):
        if event == Event.engine_move_ended:
            return Ecb.game

        return Ecb.engine_move


class GameConfig(object):
    MODE_NORMAL = 0
    MODE_LEARN = 1

    LEVEL_DISABLED = 0
    LEVEL_EASY = 1
    LEVEL_MEDIUM = 2
    LEVEL_HARD = 3

    LEVEL_LED_MAP = [
        [0, 0, 0],  # LEVEL_DISABLED
        [1, 0, 0],  # LEVEL_EASY
        [0, 1, 0],  # LEVEL_MEDIUM
        [0, 0, 1],  # LEVEL_HARD
    ]

    def __init__(self):
        self.mode = GameConfig.MODE_LEARN
        self.level = GameConfig.LEVEL_EASY
        self.opp_color = chess.WHITE
        self.time = {'min': 5, 'sec': 0}

    def mode_change(self):
        self.mode ^= 1

        return self.mode

    def opp_color_change(self):
        self.opp_color ^= 1

        return self.opp_color

    def time_change(self):
        if self.time['min'] == 0:
            self.time['min'] = 90
        else:
            self.time['min'] -= 10

    def level_change(self):
        self.level = (self.level + 1) & 0x3

    def update_clocks(self, driver):
        if self.time['min']:
            driver.clock_set(EcbDriver.CLOCK_BOTTOM, self.time['min'], 0)
            driver.clock_set(EcbDriver.CLOCK_TOP, self.time['min'], 0)
        else:
            driver.clock_blank(EcbDriver.CLOCK_BOTTOM)
            driver.clock_blank(EcbDriver.CLOCK_TOP)

    def update_leds(self, driver):
        mode_cmd = [driver.btn_led_off, driver.btn_led_on][self.mode]
        level_led_map = GameConfig.LEVEL_LED_MAP[self.level]
        color_cmd = [driver.btn_led_off, driver.btn_led_on][self.opp_color]

        mode_cmd(driver.CMD_LED_MODE)
        color_cmd(driver.CMD_LED_OPP_COLOR)

        for i in range(0, 3):
            level_cmd = [driver.btn_led_off, driver.btn_led_on][level_led_map[i]]
            level_cmd(1 << (i + 2))

    def update(self, driver):
        self.update_clocks(driver)
        self.update_leds(driver)


class Ecb(StateMachine):
    def __init__(self, driver, path_to_engine):
        self.event_queue = Queue.Queue()
        self.driver = driver
        self.driver.set_callbacks(self._sensors_callback,
                                  self._clock_expired_callback,
                                  self._cmd_callback)
        self.game_config = GameConfig()
        self.game_config.update(self.driver)

        self.board = None

        self.path_to_engine = path_to_engine
        self.engine = None

        super(Ecb, self).__init__(Ecb.idle)

        print("EcbFSM ready")

    def _sensors_callback(self, changed_squares):
        print("sensors callback: " + str(changed_squares))
        self.event_queue.put((Event.sensors_changed, changed_squares))

    def _clock_expired_callback(self, clock_id):
        print("clock expired: " + str(clock_id))
        self.event_queue.put((Event.clock_expired, clock_id))

    def _cmd_callback(self, buttons_mask):
        print("buttons pressed: " + str(buttons_mask))
        if buttons_mask & EcbDriver.CMD_BTN_GAME_START:
            self.event_queue.put((Event.game_start, None))
        else:
            self.event_queue.put((Event.game_config, buttons_mask))

    def handle_events(self):
        while True:
            try:
                event, event_data = self.event_queue.get(True, 1)
                self.handle(self, event, event_data)
                self.event_queue.task_done()
            except Queue.Empty:
                pass


Ecb.idle = Idle()
Ecb.setup = Setup()
Ecb.starting = Starting()
Ecb.stopping = Stopping()
Ecb.game = Game()
Ecb.move = Move()
Ecb.engine_move = EngineMove()

if __name__ == "__main__":
    driver = EcbDriver()
    ecb = Ecb(driver, '/home/root/stockfish')
    ecb.handle_events()
