#!/usr/bin/env python

from EcbDriver import EcbDriver
import chess
import chess.uci
import chess.polyglot
import Queue
from threading import Timer


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

Event.stray_events = Event("too many square events received")
Event.error_end = Event("error condition has ended")

Event.move_started = Event("move started")
Event.move_ended = Event("move ended")
Event.move_aborted = Event("move aborted")

Event.engine_move_started = Event("engine indicated a move")
Event.engine_move_ended = Event("engine piece was moved")

Event.promotion_started = Event("piece promotion started")
Event.promotion_ended = Event("piece promotion ended")


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
        self.ignore_sensor_events = False
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
            print("Custom position...")
            # ecb.board = chess.Board("4k3/7P/8/8/8/8/p7/4K3 w - - 0 1")
            return

        ecb.driver.leds_blink()

        if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
            print("Play against engine. Starting engine...")
            ecb.opening_book = chess.polyglot.MemoryMappedReader(ecb.path_to_opening_book)
            ecb.engine = chess.uci.popen_engine(ecb.path_to_engine)
            ecb.engine.uci()
            skill_level = 20
            if ecb.game_config.level == GameConfig.LEVEL_EASY:
                skill_level = 0
            elif ecb.game_config.level == GameConfig.LEVEL_MEDIUM:
                skill_level = 10

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

        if event == Event.sensors_changed and not self.ignore_sensor_events:
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
            ecb.driver.clock_stop(EcbDriver.CLOCK_BOTTOM)
            ecb.driver.clock_stop(EcbDriver.CLOCK_TOP)
            ecb.driver.clock_blank(EcbDriver.CLOCK_BOTTOM)
            ecb.driver.clock_blank(EcbDriver.CLOCK_TOP)

            if ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                ecb.engine.quit()
                ecb.opening_book.close()

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
            ecb.bestmove, ecb.pondermove = command.result()
            print("bestmove move: %s, ponder: %s" %
                  (ecb.bestmove.uci(), ecb.pondermove.uci()))

            ecb.event_queue.put((Event.engine_move_started,
                                 (ecb.bestmove, ecb.pondermove)))

        def opening_book_find():
            #                       EASY       MEDIUM        HARD
            weight_proportions = [(0, 0.33), (0.33, 0.66), (0.66, 1)]
            moves = list(ecb.opening_book.find_all(ecb.board))

            if not len(moves):
                return False

            max_available_weight = moves[0].weight
            min_prop, max_prop = weight_proportions[ecb.game_config.level - 1]
            min_weight = min_prop * max_available_weight
            max_weight = max_prop * max_available_weight

            exclude_moves = []
            for entry in moves:
                if entry.weight < min_weight and entry.weight > max_weight:
                    exclude_moves.append(entry.move())

            try:
                entry = ecb.opening_book.choice(ecb.board,
                                                exclude_moves=exclude_moves)
                print("opening database move: " + entry.move().uci())
                ecb.event_queue.put((Event.engine_move_started,
                                     (entry.move(), None)))
                return True
            except IndexError:
                return False

        wtime = ecb.time[chess.WHITE]
        btime = ecb.time[chess.BLACK]
        wtime_msec = (wtime['min'] * 60 + wtime['sec']) * 1000
        btime_msec = (btime['min'] * 60 + btime['sec']) * 1000

        if not opening_book_find():
            ecb.engine.position(ecb.board)
            ecb.engine.go(wtime=wtime_msec, btime=btime_msec,
                          async_callback=engine_on_go_finished)

    def run(self, ecb, event, event_data):
        print("game: " + str(event))

        if event == Event.game_started:
            ecb.driver.clock_start(ecb.board.turn)

            self.pondermove = None
            if ecb.board.turn == ecb.game_config.opp_color and\
                    ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                self._engine_go(ecb)

            ecb.driver.btn_led_on(EcbDriver.CMD_LED_START)

        if event == Event.sensors_changed:
            if len(event_data) > 2:
                ecb.event_queue.put((Event.stray_events, event_data))
                return

            if len(event_data) == 2:
                sq1 = chess.SQUARE_NAMES.index(event_data[0])
                sq2 = chess.SQUARE_NAMES.index(event_data[1])
                if ecb.board.piece_at(sq1) and ecb.board.piece_at(sq2):
                    ecb.event_queue.put((Event.stray_events, event_data))
                else:
                    if ecb.board.piece_at(sq1):
                        self.from_sq = sq1
                        event_data.pop(event_data.index(event_data[0]))
                    else:
                        self.from_sq = sq2
                        event_data.pop(event_data.index(event_data[1]))

                    ecb.event_queue.put((Event.move_ended, event_data))

                return

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
            promotion = event_data[1]

            move = chess.Move(from_square=self.from_sq,
                              to_square=to_sq,
                              promotion=promotion)

            ecb.driver.clock_stop(ecb.board.turn)
            ecb.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)
            ecb.board.push(move)

            if ecb.board.is_game_over():
                ecb.event_queue.put((Event.game_over, None))
                return

            if ecb.board.turn == ecb.game_config.opp_color and \
                    ecb.game_config.level != GameConfig.LEVEL_DISABLED:
                if ecb.pondering_on and move == ecb.pondermove:
                    print("we've got a ponderhit")
                    ecb.engine.ponderhit()
                else:
                    if ecb.pondering_on:
                        print("we've got a ponder miss")
                        ecb.pondering_on = False
                        ecb.engine.stop()

                    self._engine_go(ecb)

            ecb.driver.clock_start(ecb.board.turn)

        if event == Event.game_config_btn:
            if not event_data & EcbDriver.CMD_BTN_MODE:
                return

            ecb.game_config.mode_change()
            ecb.game_config.update_leds(ecb.driver)

    def next(self, event):
        if event == Event.game_start_btn:
            return Ecb.game_pause
        elif event == Event.move_started:
            return Ecb.move
        elif event == Event.engine_move_started:
            return Ecb.engine_move
        elif event == Event.clock_expired or event == Event.game_over:
            return Ecb.game_end
        elif event == Event.stray_events:
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

    def run(self, ecb, event, event_data):
        if event == Event.move_started:
            self.sq_from = event_data['from'][0]
            self.legal_moves = event_data['legal_moves']

            ecb.driver.leds_blink([self.sq_from])

            if ecb.game_config.mode == GameConfig.MODE_LEARN:
                ecb.driver.leds_on(self.legal_moves)

        if event == Event.sensors_changed:
            if len(event_data) != 1:
                ecb.event_queue.put((Event.stray_events, event_data))
                return

            if event_data != self.sq_from and\
                    event_data[0] not in self.legal_moves:
                return

            if self.sq_from == event_data:
                ecb.event_queue.put((Event.move_aborted, None))

            if ecb.game_config.mode == GameConfig.MODE_LEARN:
                ecb.driver.leds_off(self.legal_moves)

            ecb.driver.leds_blink()

            self.sq_to = event_data[0]

            if self._is_promotion(ecb, self.sq_from, self.sq_to):
                ecb.event_queue.put((Event.promotion_started, self.sq_to))
            else:
                ecb.event_queue.put((Event.move_ended, (self.sq_to, None)))

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
    def _engine_go_ponder(self, ecb, pondermove):
        def engine_on_go_finished(command):
            if not ecb.pondering_on:
                return

            ecb.bestmove, ecb.pondermove = command.result()
            print("ponder finished: bestmove: %s, ponder: %s" %
                  (ecb.bestmove.uci(), ecb.pondermove.uci()))

            ecb.event_queue.put((Event.engine_move_started,
                                 (ecb.bestmove, ecb.pondermove)))

        wtime = ecb.time[chess.WHITE]
        btime = ecb.time[chess.BLACK]
        wtime_msec = (wtime['min'] * 60 + wtime['sec']) * 1000
        btime_msec = (btime['min'] * 60 + btime['sec']) * 1000

        temp_board = ecb.board.copy()
        temp_board.push(pondermove)

        ecb.engine.position(temp_board)
        ecb.engine.go(wtime=wtime_msec, btime=btime_msec, ponder=True,
                      async_callback=engine_on_go_finished)

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

    def run(self, ecb, event, event_data):
        if event == Event.engine_move_started:
            bestmove = event_data[0]
            pondermove = event_data[1]

            self.from_sq = chess.SQUARE_NAMES[bestmove.from_square]
            self.to_sq = chess.SQUARE_NAMES[bestmove.to_square]
            self.promotion = bestmove.promotion

            ecb.driver.leds_blink([self.from_sq], [self.to_sq])
            ecb.driver.clock_stop(ecb.board.turn)
            ecb.time[ecb.board.turn] = ecb.driver.clock_get(ecb.board.turn)
            ecb.board.push(bestmove)

            if self.promotion is not None:
                ecb.driver.btn_led_off(0xff)
                self._signal_promotion(ecb, self.promotion)

            if ecb.board.is_game_over():
                ecb.event_queue.put((Event.game_over, None))
                return

            if pondermove is not None and\
                    ecb.game_config.level == GameConfig.LEVEL_HARD:
                print("activate pondering")
                self._engine_go_ponder(ecb, pondermove)
                ecb.pondering_on = True

            ecb.driver.clock_start(ecb.board.turn)

        if event == Event.sensors_changed:
            if len(event_data) != 1:
                raise Exception('TODO: handle multiple pieces moved')

            if event_data[0] == self.from_sq:
                ecb.driver.leds_blink(None, [self.to_sq])
                self.from_sq = ''
            elif event_data[0] == self.to_sq and self.from_sq == '':
                ecb.driver.leds_blink()

                if self.promotion is not None:
                    self.promotion_interval.cancel()
                    ecb.game_config.update_leds(ecb.driver)

                ecb.event_queue.put((Event.engine_move_ended, None))

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
    def run(self, ecb, event, event_data):
        if event == Event.stray_events:
            self.sq_list = event_data

            ecb.driver.leds_blink(event_data)

        if event == Event.sensors_changed:
            for sq in event_data:
                if sq in self.sq_list:
                    self.sq_list.pop(self.sq_list.index(sq))
                else:
                    self.sq_list.append(sq)

            ecb.driver.leds_blink(self.sq_list)

            if not len(self.sq_list):
                ecb.event_queue.put((Event.error_end, None))

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

    def _engine_go(self, ecb):
        def engine_on_go_finished(command):
            ecb.bestmove, ecb.pondermove = command.result()
            print("bestmove move: %s, ponder: %s" %
                  (ecb.bestmove.uci(), ecb.pondermove.uci()))

            ecb.event_queue.put((Event.engine_move_started,
                                 (ecb.bestmove, ecb.pondermove)))

        wtime = ecb.time[chess.WHITE]
        btime = ecb.time[chess.BLACK]
        wtime_msec = (wtime['min'] * 60 + wtime['sec']) * 1000
        btime_msec = (btime['min'] * 60 + btime['sec']) * 1000

        ecb.engine.position(ecb.board)
        ecb.engine.go(wtime=wtime_msec, btime=btime_msec,
                      async_callback=engine_on_go_finished)

    def run(self, ecb, event, event_data):
        if event == Event.game_start_btn:
            if self.timer is None:
                if not self.paused:
                    print("pausing....")
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
                        self._engine_go(ecb)

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

    def next(self, event):
        if event == Event.game_force_stop:
            return Ecb.stopping
        elif event == Event.game_resume:
            return Ecb.game

        return Ecb.game_pause


class PiecePromotion(State):
    def run(self, ecb, event, event_data):
        if event == Event.promotion_started:
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

        if event == Event.game_config_btn or event == Event.game_start_btn:
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

    def next(self, event):
        if event == Event.move_ended:
            return Ecb.game

        return Ecb.piece_promotion


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
        self.level = GameConfig.LEVEL_HARD
        self.opp_color = chess.BLACK
        self.time = {'min': 45, 'sec': 0}

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
    def __init__(self, driver, path_to_engine, path_to_opening_book):
        self.event_queue = Queue.Queue()
        self.driver = driver
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

        self.time = [self.game_config.time, self.game_config.time]

        self.bestmove = None
        self.pondermove = None
        self.pondering_on = False

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
    driver = EcbDriver()
    ecb = Ecb(driver, '/home/root/stockfish', '/home/root/ProDeo-3200.bin')
    ecb.handle_events()
