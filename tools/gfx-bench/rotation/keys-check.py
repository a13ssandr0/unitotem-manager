#!/usr/bin/env python3
"""
Runs ON the board: presses real keys through a virtual keyboard (uinput) and checks what the viewer's window did.
A marker is set in the window's page over DevTools; a key that reloaded or replaced the page loses it, and a key that opened
developer tools or a window adds a DevTools target. Usage: keys-check.py [window id]
"""
import asyncio
import fcntl
import json
import os
import struct
import sys
import time
import urllib.request

import websockets

UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x40045564, 0x40045565, 0x405c5503, 0x5501, 0x5502
KEYS = {'ctrl': 29, 'shift': 42, 'alt': 56, 'f5': 63, 'f12': 88, 'i': 23, 'j': 36, 'p': 25, 'u': 22, 's': 31, 'r': 19,
        'l': 38, 't': 20, 'n': 49, 'w': 17, 'left': 105, 'a': 30, 'c': 46, 'meta': 125, 'return': 28, 'space': 57, 'tab': 15, 'f4': 62, 'd': 32, 'esc': 1}
CASES = [('F12', ['f12']), ('Ctrl+Shift+I', ['ctrl', 'shift', 'i']), ('Ctrl+Shift+J', ['ctrl', 'shift', 'j']),
         ('Ctrl+U', ['ctrl', 'u']), ('Ctrl+P', ['ctrl', 'p']), ('Ctrl+S', ['ctrl', 's']), ('Ctrl+L', ['ctrl', 'l']),
         ('Ctrl+T', ['ctrl', 't']), ('Ctrl+N', ['ctrl', 'n']), ('Alt+Left', ['alt', 'left']), ('F5', ['f5']),
         ('Ctrl+R', ['ctrl', 'r']), ('Ctrl+Shift+R', ['ctrl', 'shift', 'r'])]
# the compositor's own default shortcuts (KEYS=compositor): they act before the window sees the key
if os.environ.get('KEYS') == 'compositor':
    CASES = [('Alt+F4', ['alt', 'f4']), ('Win+Enter', ['meta', 'return']), ('Alt+Space', ['alt', 'space']),
             ('Alt+Tab', ['alt', 'tab']), ('Win+D', ['meta', 'd']), ('Win+Left', ['meta', 'left']), ('Alt+Esc', ['alt', 'esc'])]

fd = os.open('/dev/uinput', os.O_WRONLY)
fcntl.ioctl(fd, UI_SET_EVBIT, 1)
for code in KEYS.values():
    fcntl.ioctl(fd, UI_SET_KEYBIT, code)
fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack('<HHHH80sI', 3, 0x1209, 0x7003, 1, b'UniTotem Test Keyboard', 0))
fcntl.ioctl(fd, UI_DEV_CREATE)
time.sleep(1.5)                                 # the compositor picks the new device up


def ev(kind, code, value):
    os.write(fd, struct.pack('<qqHHi', 0, 0, kind, code, value))


def press(names):
    for n in names:
        ev(1, KEYS[n], 1); ev(0, 0, 0); time.sleep(0.03)
    time.sleep(0.15)
    for n in reversed(names):
        ev(1, KEYS[n], 0); ev(0, 0, 0); time.sleep(0.03)


def targets():
    return json.load(urllib.request.urlopen('http://127.0.0.1:9223/json'))


async def main(window):
    page = next(t for t in targets() if t['type'] == 'page' and f'window={window}' in t['url'])
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
        counter = 0

        async def call(method, **params):
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({'id': counter, 'method': method, 'params': params}))
            while True:
                reply = json.loads(await ws.recv())
                if reply.get('id') == counter:
                    return reply

        await call('Runtime.evaluate', expression='window.__keysMarker = 1; document.hasFocus()')
        await call('Runtime.evaluate', expression="if (!window.__seen) { window.__seen = []; addEventListener('keydown', e => __seen.push(e.key), true) }")
        base = len(targets())
        failed = 0
        for label, names in CASES:
            await call('Runtime.evaluate', expression='window.__keysMarker = 1; window.__seen.length = 0')
            press(names)
            await asyncio.sleep(0.8)
            alive = (await call('Runtime.evaluate', expression='window.__keysMarker === 1'))['result']['result']['value']
            count = len(targets())
            seen = (await call('Runtime.evaluate', expression='JSON.stringify(window.__seen.filter(k => !["Control", "Shift", "Alt"].includes(k)))'))['result']['result']['value']
            ok = alive and count == base and seen == '[]'        # the key reached nobody: not the browser, not the page
            failed += not ok
            print(f'{label:14} page kept: {alive}  targets: {count} (was {base})  reached the page: {seen}  {"ok" if ok else "FAILED"}', flush=True)
        # a plain key and an editing shortcut still reach the page (a listener left by an earlier run on the same page doubles
        # each key: consecutive repeats are folded)
        await call('Runtime.evaluate', expression="if (!window.__keys) addEventListener('keydown', e => __keys.push((e.ctrlKey ? 'C-' : '') + e.key)); window.__keys = []")
        press(['a']); press(['ctrl', 'a']); press(['ctrl', 'c'])
        await asyncio.sleep(0.5)
        got = (await call('Runtime.evaluate', expression='JSON.stringify(window.__keys.filter((k, i, all) => k !== all[i - 1]))'))['result']['result']['value']
        print('page received:', got, 'ok' if got == '["a","C-a","C-c"]' else 'FAILED')
        failed += got != '["a","C-a","C-c"]'
    fcntl.ioctl(fd, UI_DEV_DESTROY)
    sys.exit(1 if failed else 0)


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
