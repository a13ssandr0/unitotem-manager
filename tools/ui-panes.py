#!/usr/bin/env python3
"""
Page logic of the viewer's window layout (panes, area), driven in a local headless Chrome over the DevTools protocol:

    .venv/bin/python tools/ui-panes.py

Loads manager/plugins/unitotem_viewer/static/boot-screen.html from the file system and calls its functions the way the
viewer does (setWindowLayout, show, setAssetAudio, refreshPage, showSaver): zoom and pinch per pane, refresh, the
screensaver's "is there something to play", and what the page does with a layout it should not trust. Needs
google-chrome and the `websockets` package; no node, no network.
"""
import asyncio
import json
import pathlib
import subprocess
import tempfile
import time
import urllib.request

import websockets

class Cdp:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.events = ws, 0, {}, []

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg:
                self.pending.pop(msg['id']).set_result(msg)
            else:
                self.events.append(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await fut).get('result', {})

    async def eval(self, expr, promise=False):
        r = await self.call('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=promise)
        if 'exceptionDetails' in r:
            raise RuntimeError(json.dumps(r['exceptionDetails'])[:400])
        return r.get('result', {}).get('value')


ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = f'file://{ROOT}/manager/plugins/unitotem_viewer/static/boot-screen.html?window=0'
results = []


def check(label, ok, detail=''):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + label, detail)


async def main():
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9446', '--no-sandbox',
                               f'--user-data-dir={profile}', '--window-size=1280,720', '--allow-file-access-from-files',
                               '--disable-web-security', PAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9446/json'))
                tab = next(t for t in tabs if t['type'] == 'page' and 'boot-screen' in t['url'])
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            asyncio.create_task(c.pump())
            await c.call('Runtime.enable')
            await asyncio.sleep(1)
            tree = {'kind': 'split', 'dir': 'h', 'ratio': 0.5, 'a': {'kind': 'leaf', 'id': 1}, 'b': {'kind': 'leaf', 'id': 2}}
            full = {'left': 0, 'top': 0, 'width': 100, 'height': 100, 'margin_color': 'rgb(0,0,0)'}
            await c.eval(f'setWindowLayout({json.dumps(full)}, {json.dumps(tree)})')
            web = 'data:text/html,<body>hello</body>'
            await c.eval(f'show("{web}", "web", "contain", null, 1, false, 2, true, false, 1)')           # zoom 2, pinch allowed
            await c.eval(f'show("{web}", "web", "contain", null, 1, false, 1, false, false, 2)')          # zoom 1, no pinch
            await asyncio.sleep(0.5)
            z1 = await c.eval('stages.get(1).containers.web.style.cssText')
            z2 = await c.eval('stages.get(2).containers.web.style.cssText')
            check('zoom is per pane', 'scale(2)' in z1 and 'scale' not in z2, f'{z1!r} / {z2!r}')
            t1 = await c.eval('stages.get(1).el.style.touchAction')
            t2 = await c.eval('stages.get(2).el.style.touchAction')
            th = await c.eval('document.documentElement.style.touchAction')
            check('pinch is per pane, the page allows it while one does', (t1, t2, th) == ('auto', 'pan-x pan-y', 'auto'), (t1, t2, th))
            await c.eval(f'show("{web}", "web", "contain", null, 1, false, 1, false, false, 1)')
            check('the page forbids a pinch when no pane allows it', await c.eval('document.documentElement.style.touchAction') == 'pan-x pan-y')
            before = await c.eval('stages.get(1).containers.web.src')
            check('refreshPage reloads the web panes and says so', await c.eval('refreshPage()') is True)
            await c.eval('show("data:text/html,idle", "web", "contain", null, 1, false, 1, false, true, 1)')
            await c.eval('show("data:text/html,idle", "web", "contain", null, 1, false, 1, false, true, 2)')
            check('an idle page is not reloaded', await c.eval('refreshPage()') is False)
            check('nothing to play: no screensaver', await c.eval('(saver.on = true, showSaver())') is False)
            await c.eval(f'show("{web}", "web", "contain", null, 1, false, 1, false, false, 2)')
            check('a real page in one pane is enough for the screensaver', await c.eval('showSaver()') is True)
            await c.eval('saver.on = saver.up = false; delete document.body.dataset.saver')

            # junk: sanitised, never fatal
            await c.eval('setWindowLayout({left: "x", width: -1, margin_color: "red;background:url(x)"}, 5)')
            area = await c.eval('areaEl.style.cssText')
            check('a junk area is clamped, a junk tree ignored', 'width: 5%' in area and await c.eval('stages.size') == 2, area)
            check('a junk colour is replaced', await c.eval('document.body.style.backgroundColor') == 'rgb(0, 0, 0)')
            dup = {'kind': 'split', 'dir': 'h', 'a': {'kind': 'leaf', 'id': 1}, 'b': {'kind': 'leaf', 'id': 1}}
            await c.eval(f'setWindowLayout({json.dumps(full)}, {json.dumps(dup)})')
            check('duplicate pane ids are ignored', await c.eval('stages.size') == 2)
            deep = {'kind': 'leaf', 'id': 1}
            for i in range(2, 9):
                deep = {'kind': 'split', 'dir': 'h', 'a': deep, 'b': {'kind': 'leaf', 'id': i}}
            await c.eval(f'setWindowLayout({json.dumps(full)}, {json.dumps(deep)})')
            check('a tree that is too deep is ignored', await c.eval('stages.size') == 2)
            nine = {'kind': 'leaf', 'id': 1}
            for i in range(2, 10):
                nine = {'kind': 'split', 'dir': 'v' if i % 2 else 'h', 'a': nine, 'b': {'kind': 'leaf', 'id': i}} if i < 5 else {
                    'kind': 'split', 'dir': 'h', 'a': nine, 'b': {'kind': 'leaf', 'id': i}}
            await c.eval(f'setWindowLayout({json.dumps(full)}, {json.dumps(nine)})')
            check('more than 8 panes is ignored', await c.eval('stages.size') == 2)

            # first pane re-keyed when the first leaf has another id (a main pane closed on an earlier run)
            await c.call('Page.reload')
            await asyncio.sleep(1.5)
            await c.eval('setWindowLayout({}, {kind: "leaf", id: 7})')
            check('the first pane takes the id of the main pane', await c.eval('JSON.stringify([...stages.keys()])') == '[7]')
            await c.eval('show("data:text/html,x", "web", "contain", null, 1, false, 1, false, false)')   # no pane: the main one
            check('a command without a pane goes to the main pane',
                  await c.eval('stages.get(7).current && stages.get(7).current.container') == 'web')
            await c.eval('setAssetAudio(0.5, true)')
            await c.eval('show("", "video", "contain", null, 0.3, true, 1, false, false)')
            check('volume of the main pane', await c.eval('stages.get(7).video.volume') == 0.3 and await c.eval('stages.get(7).video.muted') is True)
    finally:
        chrome.terminate()
    print(f'{sum(results)}/{len(results)} checks passed')


asyncio.run(main())
