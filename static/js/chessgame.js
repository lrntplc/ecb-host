$(document).ready(function() {
	var socket = io.connect();                                  //initiated socket client
	    socket.emit('join', getParameterByName('gameid'));  //join room as defined by query parameter in URL bar

	socket.on('move', function(moveObj){ //remote move by peer
		console.log('peer move: ' + JSON.stringify(moveObj));
		var move = game.move(moveObj);
		// illegal move
		if (move === null) {
		return;
		}
		updateStatus();
		board.position(game.fen());
	});

    function to_square(row, col) {
      var columns = 'abcdefgh'.split("");

      return columns[col] + (row + 1);
    }

    var ocuppied_squares = [];
    var current_piece = '';

    socket.on('sensors_map', function(sensors_map) {
      ocuppied_squares = [];

      for (var row = 0; row < 8; row++) {
          for (var col = 0; col < 8; col++) {
              var square = to_square(row, col);
              var squareEl = $('#board .square-' + square);
              if (sensors_map[row] & (1 << col)) {
                  ocuppied_squares.push(square);

                  if (game.get(square) != null)
                    socket.emit("square_set", square);

                  var background = '#a9a9a9';

                  if (squareEl.hasClass('black-3c85d') === true) {
                      background = '#696969';
                  }

                  squareEl.css('background', background);
              } else {
                  game.remove(square);
                  board.position(game.fen());
                  squareEl.css('background', '');
              }
          }
      }
      updateStatus();
    });

    socket.on('start_game', function(fen_string) {
      game_started = true;
      game.load(fen_string);
      board.position(game.fen());
      updateStatus();

      var i;
      for (i = 0; i < ocuppied_squares.length; i++) {
        var squareEl = $('#board .square-' + ocuppied_squares[i]);
        squareEl.css('background', '');
      }
    });

    socket.on('setup_game', function() {
      game_started = false;
      game = new Chess('8/8/8/8/8/8/8/8 w - - 0 1');
      board.position(game.fen());
    });

    socket.on('board_update', function(fen_string) {
      game.load(fen_string);
      board.position(game.fen());

      updateStatus();
    });

	var board,
	  game = new Chess('8/8/8/8/8/8/8/8 w - - 0 1'),
	  statusEl = $('#status'),
	  fenEl = $('#fen'),
      black_castling_king = $('#cr_black_king'),
      black_castling_queen = $('#cr_black_queen'),
      white_castling_king = $('#cr_white_king'),
      white_castling_queen = $('#cr_white_queen'),
      turn_white = $('#turn_white'),
      turn_black = $('#turn_black'),
      game_started = false;

    black_castling_king.on('click', function() {
      updateStatus();
    });

    black_castling_queen.on('click', function() {
      updateStatus();
    });

    white_castling_king.on('click', function() {
      updateStatus();
    });

    white_castling_queen.on('click', function() {
      updateStatus();
    });

    turn_white.on('click', function() {
      updateStatus();
    });

    turn_black.on('click', function() {
      updateStatus();
    });

	// do not pick up pieces if the game is over
	// only pick up pieces for the side to move
	var onDragStart = function(source, piece, position, orientation) {
      if (game_started)
        return false;

      current_piece = piece.toLowerCase();
	};

	var onDrop = function(source, target) {
      if (target == "offboard") {
        game.remove(source);
        board.position(game.fen());
        updateStatus();
        socket.emit('square_unset', source);
        return;
      }

      if (ocuppied_squares.indexOf(target) == -1)
        return 'snapback';

      game.put({type: current_piece[1], color: current_piece[0]}, target);
      board.position(game.fen());
      updateStatus();
      socket.emit('square_set', target);

      var i;
      var all_squares_set = true;
      for (i = 0; i < ocuppied_squares.length; i++) {
        var sq_found = false;
        var ocuppied_sq = ocuppied_squares[i];
        var sq;

        for (sq in board.position()) {
          if (ocuppied_sq == sq)
            sq_found = true;
        }

        if (!sq_found) {
          all_squares_set = false;
          break;
        }
      }

      if (all_squares_set)
        socket.emit("setup_done", game.fen());
	};

	// update the board position after the piece snap 
	// for castling, en passant, pawn promotion
	var onSnapEnd = function() {
//	  fenEl.html(board.fen());
//	  board.position(game.fen());
	};

	var updateStatus = function() {
	  var status = '';

	  var moveColor = 'White';
	  if (game.turn() === 'b') {
		moveColor = 'Black';
	  }

	  // checkmate?
	  if (game.in_checkmate() === true) {
		status = 'Game over, ' + moveColor + ' is in checkmate.';
	  }

	  // draw?
	  else if (game.in_draw() === true) {
		status = 'Game over, drawn position';
	  }

	  // game still on
	  else {
		status = moveColor + ' to move';

		// check?
		if (game.in_check() === true) {
		  status += ', ' + moveColor + ' is in check';
		}
	  }

      var castling = "";

      if (white_castling_king.is(':checked'))
        castling += "K";

      if (white_castling_queen.is(':checked'))
        castling += "Q";

      if (black_castling_king.is(':checked'))
        castling += "k";

      if (black_castling_queen.is(':checked'))
        castling += "q";

      if (castling === "")
        castling = "-";

      if (turn_white.is(':checked'))
        turn = ' w ';
      else
        turn = ' b ';

      fen_str = board.fen() + turn + castling + " - 0 1";

      game.clear();
      game.load(fen_str);

	  statusEl.html(status);
	  fenEl.html(game.fen());
	};

	var cfg = {
	  draggable: true,
	  onDragStart: onDragStart,
	  onDrop: onDrop,
	  dropOffBoard: 'trash',
//	  onSnapEnd: onSnapEnd,
      sparePieces: true,
	};
	board = ChessBoard('board', cfg);

	updateStatus();
});
