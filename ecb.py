#!/usr/bin/env python

#
#  Foldable Electronic Chess Board Project
#
#  This is the main file.
#
#  Copyright 2016 - Laurentiu Palcu <lpalcu@gmail.com>
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#

from EcbDriver import EcbDriver
from EcbFSM import Ecb, Event
import logging

import socketio
from flask import Flask, send_from_directory

from threading import Thread

sio = socketio.Server()
app = Flask(__name__)
driver = EcbDriver()
ecb = Ecb(driver, '/home/root/stockfish', '/home/root/ProDeo-3200.bin', sio)


@app.route('/')
def hello_world():
    return app.send_static_file('index.html')


@app.route('/img/<path:path>')
def send_img(path):
    return send_from_directory('/home/root/ecb/ecb/static/img', path)


@app.route('/js/<path:path>')
def send_js(path):
    return send_from_directory('/home/root/ecb/ecb/static/js', path)


@app.route('/css/<path:path>')
def send_css(path):
    return send_from_directory('/home/root/ecb/ecb/static/css', path)


@sio.on('join')
def connect(sid, environ):
    ecb.event_queue.put((Event.on_web_connect, None))


@sio.on('square_unset')
def square_unset(sid, square):
    ecb.event_queue.put((Event.on_web_square_unset, square))


@sio.on('square_set')
def square_set(sid, square):
    ecb.event_queue.put((Event.on_web_square_set, square))


@sio.on('setup_done')
def setup_done(sid, fen_string):
    ecb.event_queue.put((Event.on_web_board_setup_done, fen_string))


@sio.on('move')
def message(sid, data):
    print("message ", data)
    sio.emit('move', data)


if __name__ == '__main__':
    logging.basicConfig()

    thread = Thread(target=ecb.handle_events)
    thread.start()

    app.wsgi_app = socketio.Middleware(sio, app.wsgi_app)
    app.run(host='0.0.0.0', port=8080, threaded=True)

    thread.join()
