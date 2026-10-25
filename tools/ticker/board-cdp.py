#!/usr/bin/env python3
"""
Runs ON the board (the DevTools port 9223 of the viewer only listens there): talks to the viewer's own page over CDP.

    board-cdp.py shot FILE.png [WINDOW]      real pixels of the window's page (Page.captureScreenshot)
    board-cdp.py eval 'JS EXPRESSION' [WINDOW]
    board-cdp.py inspect [WINDOW]            window.utTicker.inspect() as JSON

WINDOW is the number in the page's address (boot-screen.html?window=N), default 0. Needs `websockets` (the manager's own).
"""
import asyncio
import base64
import json
import sys
import urllib.request

import websockets


def target(window):
    for t in json.load(urllib.request.urlopen('http://127.0.0.1:9223/json/list')):
        if t.get('type') == 'page' and f'window={window}' in t.get('url', ''):
            return t['webSocketDebuggerUrl']
    raise SystemExit(f'no page for window {window}')


async def main():
    cmd = sys.argv[1]
    if cmd == 'shot':
        out, window = sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '0'
    else:
        arg = sys.argv[2] if len(sys.argv) > 2 else ''
        window = sys.argv[3] if len(sys.argv) > 3 else (sys.argv[2] if cmd == 'inspect' and len(sys.argv) > 2 else '0')
    async with websockets.connect(target(window), max_size=None) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({'id': n, 'method': method, 'params': params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == n:
                    return msg.get('result', {})

        if cmd == 'shot':
            r = await call('Page.captureScreenshot', format='png')
            open(out, 'wb').write(base64.b64decode(r['data']))
            print('saved', out)
        else:
            expr = 'JSON.stringify(window.utTicker && window.utTicker.inspect())' if cmd == 'inspect' else arg
            r = await call('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=True)
            print(json.dumps(r.get('result', {}).get('value')) if 'exceptionDetails' not in r else r['exceptionDetails'])


asyncio.run(main())
