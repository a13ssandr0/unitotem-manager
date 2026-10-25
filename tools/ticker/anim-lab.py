#!/usr/bin/env python3
"""
Lab for the question "what keeps the MAIN thread busy while a transform animation runs on the compositor": headless Chrome,
a page with one big element animated in different ways, a Chromium trace per variant, counts per second of the main thread's
style/layout/pre-paint passes and of the frames drawn.

    .venv/bin/python tools/ticker/anim-lab.py
"""
import asyncio
import collections
import json
import subprocess
import tempfile
import time
import urllib.request

import websockets

PAGE = """<html><body style="margin:0;overflow:hidden;background:#000"><div id=a style="position:absolute;left:0;top:0;width:6000px;height:60px;
background:linear-gradient(90deg,#fff 0 50%,#08f 50%);font:40px sans-serif;will-change:transform">headline headline headline</div><div id=b style="position:absolute;
left:0;top:80px;width:6000px;height:60px;background:#f80;will-change:transform"></div></body></html>"""

VARIANTS = {
    'one running animation, fill both, no listener': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear',fill:'both'})",
    'one running animation, fill forwards': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear',fill:'forwards'})",
    'one running animation, fill none': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear'})",
    'running + onfinish listener': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear',fill:'both'}).onfinish=()=>{}",
    'running + a FUTURE one (startTime +30 s, fill both)': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear'});"
                                                          "const f=b.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear',fill:'both'});f.startTime=document.timeline.currentTime+30000",
    'running + a future one, fill none': "a.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear'});"
                                         "const f=b.animate([{transform:'translateX(0)'},{transform:'translateX(-5000px)'}],{duration:600000,easing:'linear'});f.startTime=document.timeline.currentTime+30000",
    'CSS animation (keyframes rule), linear': "document.head.insertAdjacentHTML('beforeend','<style>@keyframes m{to{transform:translateX(-5000px)}}</style>');a.style.animation='m 600s linear forwards'",
}


class Conn:
    def __init__(self, ws):
        self.ws, self.n, self.waiters, self.sink = ws, 0, {}, None

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg and msg['id'] in self.waiters:
                self.waiters.pop(msg['id']).set_result(msg)
            elif self.sink:
                self.sink(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await fut).get('result', {})


async def main(only):
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9355', '--no-sandbox', f'--user-data-dir={profile}',
                               '--window-size=1280,720', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                ver = json.load(urllib.request.urlopen('http://127.0.0.1:9355/json/version', timeout=2))['webSocketDebuggerUrl']
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9355/json/list', timeout=2)) if t['type'] == 'page')['webSocketDebuggerUrl']
                break
            except Exception:
                time.sleep(0.2)
        print('connecting', flush=True)
        async with websockets.connect(ver, max_size=None) as bws, websockets.connect(page, max_size=None) as pws:
            print('connected', flush=True)
            b, p = Conn(bws), Conn(pws)
            trace, done = [], asyncio.Event()

            def sink(m):
                if m.get('method') == 'Tracing.dataCollected':
                    trace.extend(m['params']['value'])
                elif m.get('method') == 'Tracing.tracingComplete':
                    done.set()
            b.sink = sink
            tasks = [asyncio.create_task(b.pump()), asyncio.create_task(p.pump())]
            for name, js in [v for i, v in enumerate(VARIANTS.items()) if i == only]:
                await p.call('Page.navigate', url='about:blank')
                await asyncio.sleep(0.5)
                await p.call('Runtime.evaluate', expression='document.open();document.write(' + json.dumps(PAGE) + ');document.close()')
                await asyncio.sleep(1)
                print('variant', name, flush=True)
                await p.call('Runtime.evaluate', expression=f'(()=>{{const a=document.getElementById("a"),b=document.getElementById("b");{js}}})()')
                await asyncio.sleep(1)
                trace.clear(); done.clear()
                await b.call('Tracing.start', traceConfig={'includedCategories': ['cc', 'viz', 'devtools.timeline'], 'recordMode': 'recordContinuously'})
                await asyncio.sleep(5)
                await b.call('Tracing.end')
                await asyncio.wait_for(done.wait(), 30)
                ts = [e['ts'] for e in trace if e.get('ph') != 'M' and 'ts' in e]
                span = (max(ts) - min(ts) - 1_000_000) / 1e6
                names = collections.Counter(e['name'] for e in trace if e.get('ph') != 'M' and e['ts'] > min(ts) + 1_000_000)
                print(flush=True, *[f"{name:55} style/layout {names['UpdateLayoutTree'] / span:5.1f}/s  prepaint {names['PrePaint'] / span:5.1f}/s  "
                      f"main frames {names['ProxyMain::BeginMainFrame'] / span:5.1f}/s  drawn {names['Display::DrawAndSwap'] / span:5.1f}/s"])
            chrome.terminate()
            import os
            os._exit(0)    # closing the websockets after the tasks were cancelled can hang
    finally:
        chrome.terminate()


import sys
if len(sys.argv) > 1:
    asyncio.run(main(int(sys.argv[1])))
else:
    for i in range(len(VARIANTS)):
        subprocess.run([sys.executable, '-u', __file__, str(i)], timeout=170)
