#!/usr/bin/env python3
"""
Runs ON the board: the pointer policy of the viewer window. The cursor is hidden at rest, a moving mouse shows it and it goes away again after a few
seconds without movement, a touch never shows it. Read from the page's computed `cursor` (what Chromium tells the compositor to draw).

    cursor-check.py [window id]
"""
import asyncio
import json
import sys
import urllib.request

import websockets


async def main(window):
    page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9223/json')) if t['type'] == 'page' and f'window={window}' in t['url'])
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

        async def cursor():
            return (await call('Runtime.evaluate', expression='getComputedStyle(document.body).cursor', returnByValue=True))['result']['result']['value']

        failures = 0

        def check(label, got, want):
            nonlocal failures
            ok = got == want
            failures += not ok
            print(f'{label:46} {got!r:8} {"ok" if ok else "FAILED, wanted " + repr(want)}', flush=True)

        await asyncio.sleep(3.5)
        check('at rest', await cursor(), 'none')
        await call('Input.dispatchMouseEvent', type='mouseMoved', x=300, y=300, pointerType='mouse')
        await call('Input.dispatchMouseEvent', type='mouseMoved', x=320, y=310, pointerType='mouse')
        await asyncio.sleep(0.3)
        check('a moving mouse', await cursor(), 'auto')
        await asyncio.sleep(1.5)
        check('1.5 s after the mouse stopped', await cursor(), 'auto')
        await asyncio.sleep(2.5)
        check('4 s after the mouse stopped', await cursor(), 'none')
        await call('Input.dispatchTouchEvent', type='touchStart', touchPoints=[{'x': 500, 'y': 400}])
        await call('Input.dispatchTouchEvent', type='touchMove', touchPoints=[{'x': 520, 'y': 410}])
        await call('Input.dispatchTouchEvent', type='touchEnd', touchPoints=[])
        await asyncio.sleep(0.4)
        check('after a touch', await cursor(), 'none')
        # the frame that shows the playlist's page has a cursor of its own: it is hidden too
        frames = [t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9223/json')) if t['type'] == 'iframe']
        for frame in frames:
            async with websockets.connect(frame['webSocketDebuggerUrl'], max_size=None) as inner:
                await inner.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': 'getComputedStyle(document.documentElement).cursor', 'returnByValue': True}}))
                value = json.loads(await inner.recv())['result']['result'].get('value')
            check('frame ' + frame['url'][:34], value, 'none')
        if not frames:
            print('(no separate frame target to look at)')
    sys.exit(1 if failures else 0)


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
