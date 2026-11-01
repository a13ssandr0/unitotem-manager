#!/usr/bin/env python3
"""
Runs ON the board: drops a link and then a file name onto the viewer's window through DevTools (`Input.dispatchDragEvent`, as a drag from outside would arrive)
and says whether the window or its frame was taken away from what it shows. A marker in the page tells a reload from nothing happening.

    drop-check.py [window id]
"""
import asyncio
import json
import sys
import time
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

        async def value(expression):
            return (await call('Runtime.evaluate', expression=expression, returnByValue=True))['result']['result'].get('value')

        await call('Runtime.evaluate', expression='window.__dropMarker = 1')
        before = await value('location.href + " | " + (document.querySelector(".pane iframe.web")?.src || "")')
        failures = 0
        for label, data in (('a link', [{'mimeType': 'text/uri-list', 'data': 'https://example.com/'}, {'mimeType': 'text/plain', 'data': 'https://example.com/'}]),
                            ('some text', [{'mimeType': 'text/plain', 'data': 'hello'}])):
            for kind in ('dragEnter', 'dragOver', 'drop'):
                await call('Input.dispatchDragEvent', type=kind, x=400, y=300, data={'items': data, 'files': [], 'dragOperationsMask': 1})
                await asyncio.sleep(0.2)
            await asyncio.sleep(2.5)
            now = await value('location.href + " | " + (document.querySelector(".pane iframe.web")?.src || "")')
            kept = await value('window.__dropMarker === 1')
            ok = kept and now == before
            failures += not ok
            print(f'{label:10} page kept: {kept}  address unchanged: {now == before}  {"ok" if ok else "FAILED"}', flush=True)
        # a page that has a drop zone of its own still gets its events
        await call('Runtime.evaluate', expression="window.__zone = 0; document.addEventListener('dragover', e => e.preventDefault()); document.addEventListener('drop', e => { e.preventDefault(); window.__zone++; })")
        for kind in ('dragEnter', 'dragOver', 'drop'):
            await call('Input.dispatchDragEvent', type=kind, x=400, y=300, data={'items': [{'mimeType': 'text/plain', 'data': 'x'}], 'files': [], 'dragOperationsMask': 1})
            await asyncio.sleep(0.2)
        got = await value('window.__zone')
        failures += got != 1
        print(f'own drop zone received {got} drop(s)  {"ok" if got == 1 else "FAILED"}', flush=True)
    sys.exit(1 if failures else 0)


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
