#!/usr/bin/env python

from EcbDriver import EcbDriver
import chess
import chess.uci
import chess.polyglot
import Queue
from threading import Timer
import struct
import logging


class MyHandler(chess.uci.InfoHandler):
    def post_info(self):
        super(MyHandler, self).post_info()
#        print(self.info)


class Interval(object):
    def __init__(self, timeout, timer_function, args):
        self.timeout = timeout
        self.timer_function = timer_function
        self.args = args
        self.timer = None

    def start(self):
        def wrapper():
            self.timer_function(self.args)
            self.start()

        self.timer = Timer(self.timeout, wrapper)
        self.timer.start()

    def cancel(self):
        if self.timer is not None:
            self.timer.cancel()


def set_interval(timeout, timer_function, args):
    def interval_wrapper():
        timer_function(args)
        set_interval(timeout, timer_function, args)

    interval = Timer(timeout, interval_wrapper)
    interval.start()

    return interval


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

Event.game_config_btn = Event("one of the config buttons was pressed")
Event.game_start_btn = Event("start game button was pressed")
Event.game_started = Event("game started")
Event.game_stopped = Event("game stopped")
Event.game_over = Event("game over")
Event.game_force_stop = Event("start button was pressed twice")
Event.game_resume = Event("game resumed")

Event.invalid_squares = Event("invalid squares")
Event.error_end = Event("error condition has ended")

Event.move_started = Event("move started")
Event.move_ended = Event("move ended")
Event.move_aborted = Event("move aborted")

Event.engine_move_started = Event("engine indicated a move")
Event.engine_move_ended = Event("engine piece was moved")

Event.promotion_started = Event("piece promotion started")
Event.promotion_ended = Event("piece promotion ended")

Event.pondering_finished = Event("engine finished pondering")

Event.on_web_connect = Event("web client connected")
Event.on_web_disconnect = Event("web client disconnected")
Event.on_web_square_set = Event("a piece on a square has been set in web client")
Event.on_web_square_unset = Event("a piece on a square has been unset in web client")
Event.on_web_board_setup_done = Event("custom board setup finished")


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
        if event == Event.game_config_btn:
            return Ecb.setup
        if event == Event.game_start_btn:
            return Ecb.starting

        return Ecb.idle


class Setup(State):
    def run(self, ecb, event, event_data):
        print("setup: " + str(event))
        if event != Event.game_config_btn:
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
        if event == Event.game_start_btn:
            return Ecb.starting

        return Ecb.setup


class Starting(State):
    POSITION_NEW = 0
    POSITION_CUSTOM = 1

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

    def _new_game_unknown_squares(self, ecb, sensors_map):
        initial_pos_rows = [0, 1, 6, 7]
        unknown_squares = []

        for i in range(0, 4):
            row_pos = initial_pos_rows[i]
            row_val = ~(sensors_map[initial_pos_rows[i]])

            unknown_squares += ecb.row_to_squares(row_pos, row_val)

        return unknown_squares

    def _attempt_start(self, ecb):
        self.ignore_sensor_events = False
        sensors_map = ecb.driver.sensors_get()
        print(str(sensors_map))
        self.position_type = self._detect_position_type(sensors_map)

        if ecb.sio is not None and self.position_type != self.POSITION_NEW and\
                ecb.custom_fen is None:
            ecb.sio.emit("sensors_map", sensors_map)

        if self.position_type == self.POSITION_NEW:
            unknown_squares = self._new_game_unknown_squares(ecb, sensors_map)
            if len(unknown_squares):
                ecb.driver.leds_blink(unknown_squares)
                return

            ecb.board = chess.Board(chess.STARTING_FEN)
        else:
            print("Custom position...")

            if ecb.custom_fen is not None:
                ecb.board = chess.Board(ecb.custom_fen)
            else:
                self.custom_squares = ecb.sensor_map_to_squares(sensors_map)
                ecb.driver.leds_blink(None, self.custom_squares)

                return

        if ecb.sio is not None:
            ecb.sio.emit("start_game", ecb.board.fen())

        ecb.driver.leds_blink()

        if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            print("Play against engine. Starting engine...")
            ecb.opening_book = chess.polyglot.MemoryMappedReader(ecb.path_to_opening_book)
            ecb.engine = chess.uci.popen_engine(ecb.path_to_engine)
            ecb.info_handler = MyHandler()
            ecb.engine.info_handlers.append(ecb.info_handler)
            ecb.engine.uci()

            if ecb.game_config.level < GameConfig.LEVEL_7:
                skill_level = ecb.ENGINE_SETTINGS[ecb.game_config.level - 1]['skill']
            else:
                skill_level = 20

            print("setting engine skill to %d." % skill_level)
            ecb.engine.setoption({
                'skill level': skill_level,
                'threads': 2
            })
            ecb.engine.ucinewgame()
        else:
            print("Play against human.")

        ecb.event_queue.put((Event.game_started, None))

    def run(self, ecb, event, event_data):
        print("starting: " + str(event))
        if event == Event.game_start_btn and not ecb.driver.sensors_running():
            ecb.game_config.update(ecb.driver)
            ecb.driver.sensors_start()

            # we need a small delay for the sensors to settle
            self.ignore_sensor_events = True
            Timer(1, self._attempt_start, [ecb]).start()

            if ecb.sio is not None:
                ecb.sio.emit("setup_game")

        if event == Event.sensors_changed and not self.ignore_sensor_events:
            self._attempt_start(ecb)

        if event == Event.on_web_connect:
            if ecb.sio is not None:
                ecb.sio.emit("setup_game")

                if self.position_type != self.POSITION_NEW and\
                        ecb.custom_fen is None:
                    ecb.sio.emit("sensors_map", ecb.driver.sensors_get())

        if event == Event.on_web_square_set:
            self.custom_squares.pop(self.custom_squares.index(event_data))
            ecb.driver.leds_blink(None, self.custom_squares)

        if event == Event.on_web_square_unset:
            self.custom_squares.append(event_data)
            ecb.driver.leds_blink(None, self.custom_squares)

        if event == Event.on_web_board_setup_done:
            ecb.custom_fen = event_data
            self._attempt_start(ecb)

    def next(self, event):
        if event == Event.game_started:
            return Ecb.game
        elif event == Event.game_start_btn:
            return Ecb.stopping

        return Ecb.starting


class Stopping(State):
    def run(self, ecb, event, event_data):
        print("stopping: " + str(event))
        if event in [Event.game_force_stop, Event.game_start_btn]:
            ecb.driver.sensors_stop()
            ecb.driver.btn_led_off(EcbDriver.CMD_LED_START)
            ecb.driver.leds_blink()

            if ecb.game_config.use_time_control():
                ecb.driver.clock_stop(EcbDriver.CLOCK_BOTTOM)
                ecb.driver.clock_stop(EcbDriver.CLOCK_TOP)

            ecb.driver.clock_set(EcbDriver.CLOCK_BOTTOM,
                                 ecb.game_config.time['min'], 0)
            ecb.driver.clock_set(EcbDriver.CLOCK_TOP,
                                 ecb.game_config.time['min'], 0)

            if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                if ecb.engine is not None:
                    ecb.engine.quit()
                if ecb.opening_book is not None:
                    ecb.opening_book.close()

            ecb.board = None
            ecb.custom_fen = None

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

    def _handle_game_started(self, ecb, event_data):
        if ecb.game_config.use_time_control():
            ecb.driver.clock_start(ecb.board.turn)
        else:
            ecb.driver.clock_blank(not ecb.board.turn)
            ecb.driver.clock_set(ecb.board.turn, 0, 0)

        self.pondermove = None
        if ecb.board.turn == ecb.game_config.opp_color and\
                ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            ecb.engine_go()

        ecb.driver.btn_led_on(EcbDriver.CMD_LED_START)

    def _handle_sensors_changed(self, ecb, event_data):
        if len(event_data) != 1:
            ecb.event_queue.put((Event.invalid_squares, event_data))
            return

        # ignore events from engine pieces
        if ecb.board.turn == ecb.game_config.opp_color and \
                ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            return

        self.from_sq = chess.SQUARE_NAMES.index(event_data[0])

        # we should have an empty square to detect a move start
        if ecb.chessman_detected(event_data[0]):
            return

        legal_moves = self._get_legal_moves(ecb.board, event_data[0])

        # of piece cannot be moved, just return
        if len(legal_moves) == 0:
            return

        move_started_ev_data = {
            'from': event_data,
            'legal_moves': legal_moves
        }
        ecb.event_queue.put((Event.move_started, move_started_ev_data))

    def _handle_move_ended(self, ecb, event_data):
        to_sq = chess.SQUARE_NAMES.index(event_data[0])
        promotion = event_data[1]

        move = chess.Move(from_square=self.from_sq,
                          to_square=to_sq,
                          promotion=promotion)

        if ecb.game_config.use_time_control():
            ecb.driver.clock_stop(ecb.board.turn)
            ecb.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)
        else:
            ecb.driver.clock_blank(ecb.board.turn)

        ecb.board.push(move)

        if ecb.sio is not None:
            ecb.sio.emit('board_update', ecb.board.fen())

        if ecb.board.is_game_over():
            ecb.event_queue.put((Event.game_over, None))
            return

        if ecb.board.turn == ecb.game_config.opp_color and \
                ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            if ecb.pondering_result is not None:
                ecb.pondering_result = None
                if move == ecb.pondermove:
                    ecb.event_queue.put((Event.engine_move_started,
                                        (ecb.pondering_result[0],
                                         ecb.pondering_result[1])))
                else:
                    ecb.engine_go()

            elif ecb.pondering_on and move == ecb.pondermove:
                print("we've got a ponderhit")
                ecb.engine.ponderhit()
            else:
                if ecb.pondering_on:
                    print("we've got a ponder miss")
                    ecb.pondering_on = False
                    ecb.engine_ignore_callback = True
                    ecb.engine.stop()

                ecb.engine_go()

        if ecb.game_config.use_time_control():
            ecb.driver.clock_start(ecb.board.turn)
        else:
            ecb.driver.clock_set(ecb.board.turn, 0, 0)

        invalid_squares_list = ecb.validate_board()
        if (len(invalid_squares_list)):
            ecb.event_queue.put((Event.invalid_squares, invalid_squares_list))

    def _handle_game_config_btn(self, ecb, event_data):
        if not event_data & EcbDriver.CMD_BTN_MODE:
            return

        ecb.game_config.mode_change()
        ecb.game_config.update_leds(ecb.driver)

    def _handle_engine_move_ended(self, ecb):
        if ecb.board.is_game_over():
            ecb.event_queue.put((Event.game_over, None))

    def _handle_pondering_finished(self, ecb, event_data):
        if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            if ecb.board.turn == ecb.game_config.opp_color:
                ecb.event_queue.put((Event.engine_move_started,
                                     (event_data[0], event_data[1])))
            else:
                ecb.pondering_result = event_data

    def _handle_error_end(self, ecb, event_data):
        # In case the engine finished while we were in an error condition,
        # re-send the event.
        if event_data is not None:
            ecb.event_queue.put((Event.engine_move_started, event_data))

    def _handle_on_web_connect(self, ecb):
        if ecb.sio is not None:
            ecb.sio.emit('start_game', ecb.board.fen())

    def run(self, ecb, event, event_data):
        print("game: " + str(event))

        if event == Event.game_started:
            self._handle_game_started(ecb, event_data)

        if event == Event.sensors_changed:
            self._handle_sensors_changed(ecb, event_data)

        if event == Event.move_ended:
            self._handle_move_ended(ecb, event_data)

        if event == Event.game_config_btn:
            self._handle_game_config_btn(ecb, event_data)

        if event == Event.engine_move_ended:
            self._handle_engine_move_ended(ecb)

        if event == Event.pondering_finished:
            self._handle_pondering_finished(ecb, event_data)

        if event == Event.error_end:
            self._handle_error_end(ecb, event_data)

        if event == Event.on_web_connect:
            self._handle_on_web_connect(ecb)

    def next(self, event):
        if event == Event.game_start_btn:
            return Ecb.game_pause
        elif event == Event.move_started:
            return Ecb.move
        elif event == Event.engine_move_started:
            return Ecb.engine_move
        elif event == Event.clock_expired or event == Event.game_over:
            return Ecb.game_end
        elif event == Event.invalid_squares:
            return Ecb.game_error

        return Ecb.game


class Move(State):
    def _is_promotion(self, ecb, move_start, move_end):
        sq_from = chess.SQUARE_NAMES.index(move_start)
        sq_to = chess.SQUARE_NAMES.index(move_end)
        piece_type = ecb.board.piece_type_at(sq_from)

        if not piece_type == chess.PAWN:
            return False

        if chess.rank_index(sq_to) == 7:
            return True

        return False

    def _handle_move_started(self, ecb, event_data):
        self.sq_from = event_data['from'][0]
        self.legal_moves = event_data['legal_moves']

        ecb.driver.leds_blink([self.sq_from])

        if ecb.game_config.mode == GameConfig.MODE_LEARN:
            ecb.driver.leds_on(self.legal_moves)

    def _handle_sensors_changed(self, ecb, event_data):
        def debounce_move():
            if self.sq_from == self.sq_to:
                ecb.event_queue.put((Event.move_aborted, None))
                ecb.driver.leds_off(self.legal_moves)
                ecb.driver.leds_blink()
                return

            ecb.driver.leds_off(self.legal_moves)
            ecb.driver.leds_blink()

            if self._is_promotion(ecb, self.sq_from, self.sq_to):
                ecb.event_queue.put((Event.promotion_started, self.sq_to))
            else:
                ecb.event_queue.put((Event.move_ended, (self.sq_to, None)))

        try:
            self.timer.cancel()
        except AttributeError:
            pass

        if len(event_data) != 1:
            ecb.event_queue.put((Event.invalid_squares, event_data))
            return

        if event_data[0] != self.sq_from and\
                event_data[0] not in self.legal_moves:
            return

        try:
            ecb.driver.leds_off([self.sq_to])
        except AttributeError:
            pass

        self.sq_to = event_data[0]

        if not ecb.chessman_detected(self.sq_to):
            return

        ecb.driver.leds_on([self.sq_to])

        self.timer = Timer(1, debounce_move)
        self.timer.start()

    def run(self, ecb, event, event_data):
        print("move: " + str(event))

        if event == Event.move_started:
            self._handle_move_started(ecb, event_data)

        if event == Event.sensors_changed:
            self._handle_sensors_changed(ecb, event_data)

        if event == Event.pondering_finished:
            ecb.pondering_result = event_data

    def next(self, event):
        if event == Event.move_ended:
            return Ecb.game
        elif event == Event.move_aborted:
            return Ecb.game
        elif event == Event.clock_expired:
            return Ecb.game_end
        elif event == Event.promotion_started:
            return Ecb.piece_promotion

        return Ecb.move


class EngineMove(State):
    def _signal_promotion(self, ecb, chess_piece_type):
        promotion_led_map = {
            chess.QUEEN: EcbDriver.CMD_LED_MODE,
            chess.ROOK: EcbDriver.CMD_LED_OPP_LEVEL0,
            chess.BISHOP: EcbDriver.CMD_LED_OPP_COLOR,
            chess.KNIGHT: EcbDriver.CMD_LED_START,
        }

        self.promotion_interval = Interval(0.5,
                                           ecb.driver.btn_led_toggle,
                                           promotion_led_map[chess_piece_type])
        self.promotion_interval.start()

    def _handle_engine_move_started(self, ecb, event_data):
        bestmove = event_data[0]
        pondermove = event_data[1]

        self.from_sq = chess.SQUARE_NAMES[bestmove.from_square]
        self.to_sq = chess.SQUARE_NAMES[bestmove.to_square]
        self.promotion = bestmove.promotion

        ecb.driver.leds_blink([self.from_sq], [self.to_sq])

        if ecb.game_config.use_time_control():
            ecb.driver.clock_stop(ecb.board.turn)
            ecb.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)
        else:
            ecb.driver.clock_blank(ecb.board.turn)

        ecb.board.push(bestmove)

        if ecb.sio is not None:
            ecb.sio.emit('board_update', ecb.board.fen())

        if self.promotion is not None:
            ecb.driver.btn_led_off(0xff)
            self._signal_promotion(ecb, self.promotion)

        if pondermove is not None and\
                ecb.game_config.level == GameConfig.LEVEL_7:
            print("activate pondering for: " + str(pondermove.uci()))
            ecb.engine_go(pondermove)

        if ecb.game_config.use_time_control():
            ecb.driver.clock_start(ecb.board.turn)
        else:
            ecb.driver.clock_set(ecb.board.turn, 0, 0)

    def _handle_sensors_changed(self, ecb, event_data):
        if len(event_data) != 1:
            ecb.event_queue.put((Event.invalid_squares, event_data))
            return

        if event_data[0] == self.from_sq:
            ecb.driver.leds_blink(None, [self.to_sq])
            self.from_sq = ''
        elif event_data[0] == self.to_sq and self.from_sq == '':
            ecb.driver.leds_blink()

            if self.promotion is not None:
                self.promotion_interval.cancel()
                ecb.game_config.update_leds(ecb.driver)

            ecb.event_queue.put((Event.engine_move_ended, None))
            invalid_squares = ecb.validate_board()
            if (len(invalid_squares)):
                ecb.event_queue.put((Event.invalid_squares, event_data))

    def run(self, ecb, event, event_data):
        print("EngineMove: " + str(event))
        if event == Event.engine_move_started:
            self._handle_engine_move_started(ecb, event_data)

        if event == Event.sensors_changed:
            self._handle_sensors_changed(ecb, event_data)

        if event == Event.pondering_finished:
            ecb.pondering_result = event_data

    def next(self, event):
        if event == Event.engine_move_ended:
            return Ecb.game
        elif event == Event.clock_expired or event == Event.game_over:
            return Ecb.game_end

        return Ecb.engine_move


class GameEnd(State):
    winner_blinking_leds = [
        ['a8', 'b8', 'c8', 'd8', 'e8', 'f8', 'g8', 'h8'],
        ['a1', 'b1', 'c1', 'd1', 'e1', 'f1', 'g1', 'h1']
    ]

    def run(self, ecb, event, event_data):
        print("GameEnd: " + str(event))
        if event == Event.clock_expired:

            ecb.driver.leds_blink(self.winner_blinking_leds[not ecb.board.turn])

        if event == Event.game_over:
            if ecb.board.is_checkmate():
                ecb.driver.leds_blink(self.winner_blinking_leds[not ecb.board.turn])
            else:
                ecb.driver.leds_blink(self.winner_blinking_leds[0] +
                                      self.winner_blinking_leds[1])

    def next(self, event):
        if event == Event.game_start_btn:
            return Ecb.stopping

        return Ecb.game_end


class GameError(State):
    def __init__(self):
        self.engine_move = None

    def _handle_invalid_squares(self, ecb, event_data):
        self.sq_list = event_data
        ecb.driver.leds_blink(event_data, timeout=1)

    def _handle_sensors_changed(self, ecb, event_data):
        for sq in event_data:
            if sq in self.sq_list:
                self.sq_list.pop(self.sq_list.index(sq))
            else:
                self.sq_list.append(sq)

        ecb.driver.leds_blink(self.sq_list, timeout=1)

        if not len(self.sq_list):
            invalid_squares = ecb.validate_board()
            if len(invalid_squares):
                self.sq_list = invalid_squares
                return

            ecb.event_queue.put((Event.error_end, self.engine_move))
            self.engine_move = None

    def _handle_engine_move_started(self, ecb, event_data):
        # save the engine move until error condition is over
        self.engine_move = event_data

    def run(self, ecb, event, event_data):
        print("GameError: " + str(event))
        if event == Event.invalid_squares:
            self._handle_invalid_squares(ecb, event_data)

        if event == Event.sensors_changed:
            self._handle_sensors_changed(ecb, event_data)

        if event == Event.engine_move_started:
            self._handle_engine_move_started(ecb, event_data)

    def next(self, event):
        if event == Event.error_end:
            return Ecb.game

        return Ecb.game_error


class GamePause(State):
    def __init__(self):
        self.timer = None
        self.paused = False

    def _can_stop_timeout(self):
        self.timer = None
        self.paused = True

    def _handle_game_start_btn(self, ecb):
        if self.timer is None:
            if not self.paused:
                print("pausing....")
                if ecb.game_config.use_time_control():
                    ecb.driver.clock_stop(ecb.board.turn)
                    ecb.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)

                if ecb.board.turn == ecb.game_config.opp_color and \
                        ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                    ecb.engine.stop()

                self.led_blink = Interval(1,
                                          ecb.driver.btn_led_toggle,
                                          EcbDriver.CMD_LED_START)
                self.led_blink.start()

                self.timer = Timer(3, self._can_stop_timeout)
                self.timer.start()
            else:
                print("resuming...")
                if ecb.board.turn == ecb.game_config.opp_color and \
                        ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                    ecb.engine_go()

                if ecb.game_config.use_time_control():
                    ecb.driver.clock_start(ecb.board.turn)

                ecb.event_queue.put((Event.game_resume, None))

                if self.led_blink is not None:
                    self.led_blink.cancel()

                ecb.driver.btn_led_on(EcbDriver.CMD_LED_START)

                self.paused = False

        else:  # button was pressed the second time before the timer expire
            print("stopping...")
            ecb.event_queue.put((Event.game_force_stop, None))

            if self.led_blink is not None:
                self.led_blink.cancel()

            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

            self.paused = False

    def run(self, ecb, event, event_data):
        print("GamePause: " + str(event))

        if event == Event.game_start_btn:
            self._handle_game_start_btn(ecb)

    def next(self, event):
        if event == Event.game_force_stop:
            return Ecb.stopping
        elif event == Event.game_resume:
            return Ecb.game

        return Ecb.game_pause


class PiecePromotion(State):
    def _handle_promotion_started(self, ecb, event_data):
        self.to_sq = event_data

        led_mask = EcbDriver.CMD_LED_START |\
            EcbDriver.CMD_LED_MODE |\
            EcbDriver.CMD_LED_OPP_LEVEL0 |\
            EcbDriver.CMD_LED_OPP_COLOR

        ecb.driver.btn_led_off(0xff)
        self.blink_interval = Interval(0.5,
                                       ecb.driver.btn_led_toggle,
                                       led_mask)
        self.blink_interval.start()

    def _handle_buttons(self, ecb, event_data):
        if event_data is None:
            promotion = chess.KNIGHT
        elif event_data & EcbDriver.CMD_BTN_GAME_TIME:
            pass
        elif event_data & EcbDriver.CMD_BTN_MODE:
            promotion = chess.QUEEN
        elif event_data & EcbDriver.CMD_BTN_OPP_LEVEL:
            promotion = chess.ROOK
        else:
            promotion = chess.BISHOP

        self.blink_interval.cancel()
        ecb.game_config.update_leds(ecb.driver)

        ecb.event_queue.put((Event.move_ended, (self.to_sq, promotion)))

    def run(self, ecb, event, event_data):
        print("PiecePromotion: " + str(event))
        if event == Event.promotion_started:
            self._handle_promotion_started(ecb, event_data)

        if event == Event.game_config_btn or event == Event.game_start_btn:
            self._handle_buttons(ecb, event_data)

    def next(self, event):
        if event == Event.move_ended:
            return Ecb.game

        return Ecb.piece_promotion


class GameConfig(object):
    MODE_NORMAL = 0
    MODE_LEARN = 1

    LEVEL_DISABLED = 0
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4
    LEVEL_5 = 5
    LEVEL_6 = 6
    LEVEL_7 = 7

    LEVEL_LED_MAP = [
        [0, 0, 0],  # LEVEL_DISABLED
        [1, 0, 0],  # LEVEL_1
        [0, 1, 0],  # LEVEL_2
        [1, 1, 0],  # LEVEL_3
        [0, 0, 1],  # LEVEL_4
        [1, 0, 1],  # LEVEL_5
        [0, 1, 1],  # LEVEL_6
        [1, 1, 1],  # LEVEL_7
    ]

    def __init__(self):
        self.mode = GameConfig.MODE_LEARN
        self.level = GameConfig.LEVEL_DISABLED
        self.opp_color = chess.BLACK
        self.time = {'min': 45, 'sec': 0}
        self.time_controlled = True

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
            if self.time['min'] > 15:
                step = 15
            else:
                step = 5

            self.time['min'] -= step

    def level_change(self):
        self.level = (self.level + 1) & 0x7

    def update_clocks(self, driver):
        driver.clock_set(EcbDriver.CLOCK_BOTTOM, self.time['min'], 0)
        driver.clock_set(EcbDriver.CLOCK_TOP, self.time['min'], 0)

        if self.time['min'] == 0:
            self.time_controlled = False

            return

        self.time_controlled = True

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

    def use_time_control(self):
        return self.time_controlled


class Ecb(StateMachine):
    # engine settings for levels 1-6
    ENGINE_SETTINGS = [
        {'skill': 0, 'depth': 1, 'movetime': 30},    # LEVEL 1
        {'skill': 3, 'depth': 1, 'movetime': 50},    # LEVEL 2
        {'skill': 6, 'depth': 2, 'movetime': 100},   # LEVEL 3
        {'skill': 9, 'depth': 3, 'movetime': 150},   # LEVEL 4
        {'skill': 11, 'depth': 4, 'movetime': 200},  # LEVEL 5
        {'skill': 17, 'depth': 8, 'movetime': 300},  # LEVEL 6
    ]

    def __init__(self, driver, path_to_engine, path_to_opening_book, sio=None):
        self.event_queue = Queue.Queue()
        self.driver = driver
        self.sio = sio
        self.driver.set_callbacks(self._sensors_callback,
                                  self._clock_expired_callback,
                                  self._cmd_callback)
        self.game_config = GameConfig()
        self.game_config.update(self.driver)

        self.board = None

        self.path_to_engine = path_to_engine
        self.path_to_opening_book = path_to_opening_book
        self.engine = None
        self.opening_book = None
        self.info_handler = None

        self.time = [self.game_config.time, self.game_config.time]

        self.bestmove = None
        self.pondermove = None
        self.pondering_on = False
        self.pondering_result = None
        self.engine_ignore_callback = False

        self.web_client_connected = False
        self.custom_fen = None

        super(Ecb, self).__init__(Ecb.idle)

        print("EcbFSM ready")

    def _opening_book_find(self):
        #     LEVELS             1-3          4-6          7
        weight_proportions = [(0, 0.33), (0.33, 0.66), (0.66, 1)]
        level_to_weight_index = [0, 0, 0, 0, 1, 1, 1, 2]
        moves = list(self.opening_book.find_all(self.board))

        if not len(moves):
            return False

        max_available_weight = moves[0].weight
        weight_index = level_to_weight_index[self.game_config.level]
        min_prop, max_prop = weight_proportions[weight_index]
        min_weight = min_prop * max_available_weight
        max_weight = max_prop * max_available_weight

        exclude_moves = []
        for entry in moves:
            if entry.weight < min_weight and entry.weight > max_weight:
                exclude_moves.append(entry.move())

        try:
            entry = self.opening_book.choice(self.board,
                                             exclude_moves=exclude_moves)
            print("opening database move: " + entry.move().uci())
            self.event_queue.put((Event.engine_move_started,
                                 (entry.move(), None)))
            return True
        except IndexError:
            return False

    def engine_go(self, pondermove=None):
        def engine_on_go_finished(command):
            if self.engine_ignore_callback:
                self.engine_ignore_callback = False
                return

            self.bestmove, self.pondermove = command.result()
            if self.pondermove is not None:
                print("bestmove move: %s, ponder: %s" %
                      (self.bestmove.uci(), self.pondermove.uci()))
            else:
                print("bestmove move: %s" % self.bestmove.uci())

            if self.pondering_on:
                self.event_queue.put((Event.pondering_finished,
                                     (self.bestmove, self.pondermove)))
            else:
                self.event_queue.put((Event.engine_move_started,
                                     (self.bestmove, self.pondermove)))

        if self.game_config.use_time_control():
            wtime = self.time[chess.WHITE]
            btime = self.time[chess.BLACK]
            wtime_msec = (wtime['min'] * 60 + wtime['sec']) * 1000
            btime_msec = (btime['min'] * 60 + btime['sec']) * 1000
        else:
            # always use 90 minutes
            wtime_msec = 90 * 60 * 1000
            btime_msec = 90 * 60 * 1000

        if not self._opening_book_find():
            if pondermove is not None:
                board = self.board.copy()
                board.push(pondermove)
                self.pondering_on = True
            else:
                board = self.board
                self.pondering_on = False

            self.engine.position(board)

            if self.game_config.level < GameConfig.LEVEL_7:
                depth = self.ENGINE_SETTINGS[self.game_config.level - 1]['depth']
                movetime = self.ENGINE_SETTINGS[self.game_config.level - 1]['movetime']

                self.engine.go(wtime=wtime_msec, btime=btime_msec,
                               ponder=self.pondering_on,
                               depth=depth, movetime=movetime,
                               async_callback=engine_on_go_finished)
            elif self.game_config.level == GameConfig.LEVEL_7:
                self.engine.go(wtime=wtime_msec, btime=btime_msec,
                               ponder=self.pondering_on,
                               async_callback=engine_on_go_finished)

    def row_to_squares(self, row, val):
        columns = "abcdefgh"
        squares = []

        for i in range(0, 8):
            if (val & (1 << i)):
                squares.append("%s%d" % (columns[i], row + 1))

        return squares

    def sensor_map_to_squares(self, sensor_map):
        squares = []
        for row in range(0, 8):
            squares += self.row_to_squares(row, sensor_map[row])

        return squares

    # returns True if sensor detects a chessman
    def chessman_detected(self, square):
        on_squares = self.sensor_map_to_squares(self.driver.sensors_get())

        if square in on_squares:
            return True

        return False

    def validate_board(self):
        if self.board is None:
            return

        piece_types = [chess.PAWN, chess.ROOK, chess.KNIGHT, chess.BISHOP, chess.QUEEN, chess.KING]
        piece_colors = [chess.WHITE, chess.BLACK]

        board_map = 0

        for pc in piece_colors:
            for pt in piece_types:
                board_map |= int(self.board.pieces(pt, pc))

        # convert to list
        board_map = struct.unpack('8B', struct.pack('Q', board_map))

        # get the sensor map
        sensors_map = self.driver.sensors_get()

        # compare the two lists and extract a list of squares that don't match

        unmatching_squares = []
        for row in range(0, 8):
            row_changes = sensors_map[row] ^ board_map[row]

            unmatching_squares += self.row_to_squares(row, row_changes)

        return unmatching_squares

    def _sensors_callback(self, changed_squares):
        print("sensors callback: " + str(changed_squares))
        self.event_queue.put((Event.sensors_changed, changed_squares))

    def _clock_expired_callback(self, clock_id):
        print("clock expired: " + str(clock_id))
        self.event_queue.put((Event.clock_expired, clock_id))

    def _cmd_callback(self, buttons_mask):
        print("buttons pressed: " + str(buttons_mask))
        if buttons_mask & EcbDriver.CMD_BTN_GAME_START:
            self.event_queue.put((Event.game_start_btn, None))
        else:
            self.event_queue.put((Event.game_config_btn, buttons_mask))

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
Ecb.game_end = GameEnd()
Ecb.game_error = GameError()
Ecb.game_pause = GamePause()
Ecb.move = Move()
Ecb.engine_move = EngineMove()
Ecb.piece_promotion = PiecePromotion()

if __name__ == "__main__":
    logging.basicConfig()
    driver = EcbDriver()
    ecb = Ecb(driver, '/home/root/stockfish', '/home/root/ProDeo-3200.bin')
    ecb.handle_events()
