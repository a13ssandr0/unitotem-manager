#!/usr/bin/env python3
"""
The Developer tools button of the Viewers page, in a real (headless) Chrome: logs in, opens /viewers, clicks the button of the
first window and checks that a tab with the front end opens and is connected to the page of the window (its Elements panel
shows the page's own document). Screenshots go to --out-dir.

    tools/ui-devtools.py --host <host> --user root --password unitotem --out-dir /tmp/devtools-shots
"""
import argparse
import asyncio
import base64
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

PORT = 9345             # not the ports of the other UI tools: running two at once gives garbage


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

    async def eval(self, expression, await_promise=False):
        r = await self.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=await_promise)
        return r.get('result', {}).get('value')


def targets():
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list'))


async def attach(target):
    ws = await websockets.connect(target['webSocketDebuggerUrl'], max_size=None)
    cdp = Cdp(ws)
    asyncio.get_running_loop().create_task(cdp.pump())
    await cdp.call('Page.enable')
    await cdp.call('Runtime.enable')
    await cdp.call('Network.enable')
    return cdp


async def shot(cdp, path):
    data = (await cdp.call('Page.captureScreenshot', format='png'))['data']
    Path(path).write_bytes(base64.b64decode(data))


async def run(args):
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chrome = subprocess.Popen(['google-chrome', '--headless=new', f'--remote-debugging-port={PORT}', '--no-sandbox',
                               '--ignore-certificate-errors', '--disable-popup-blocking', f'--user-data-dir={tempfile.mkdtemp()}', '--window-size=1400,1000',
                               'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []
    try:
        for _ in range(50):
            try:
                page = next(t for t in targets() if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        cdp = await attach(page)
        base = f'https://{args.host}'
        await cdp.call('Page.navigate', url=base + '/login')
        await asyncio.sleep(2)
        status = await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                                f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
        print('login', status)
        await cdp.call('Page.navigate', url=base + '/viewers')
        await asyncio.sleep(6)
        await shot(cdp, out / 'viewers.png')
        row = await cdp.eval("[...document.querySelectorAll('tr')].some(r => r.innerText.includes('Developer tools'))")
        print('row shown:', row)
        if not row:
            failures.append('no Developer tools row')
        before = {t['id'] for t in targets()}
        clicked = await cdp.eval("""(() => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes('Developer tools'));
          const b = row && row.querySelector('button'); if (b) b.click(); return !!b; })()""")
        print('clicked:', clicked)
        tab = None
        for _ in range(40):
            await asyncio.sleep(0.5)
            tab = next((t for t in targets() if t['id'] not in before and '/devtools/inspector.html' in t['url']), None)
            if tab:
                break
        print('devtools tab:', tab['url'][:90] if tab else None)
        if not tab:
            failures.append('the front end did not open')
        else:
            tools = await attach(tab)
            await asyncio.sleep(args.wait)
            await shot(tools, out / 'devtools.png')
            requests = [e['params'] for e in tools.events if e['method'] == 'Network.responseReceived']
            failed = [e['params'] for e in tools.events if e['method'] == 'Network.loadingFailed']
            print(f'requests answered: {len(requests)}, failed: {len(failed)}')
            bad = [(r['response']['status'], r['response']['url'][-70:]) for r in requests if r['response']['status'] >= 400]
            print('HTTP errors:', bad[:8])
            print('failed:', [(f.get('errorText'), f.get('blockedReason')) for f in failed[:5]])
            errors = [e['params'] for e in tools.events if e['method'] == 'Runtime.exceptionThrown']
            print('exceptions:', [x['exceptionDetails'].get('exception', {}).get('description', x['exceptionDetails']['text'])[:160] for x in errors[:3]])
            title = await tools.eval('document.title')
            print('title:', title)
            # connected: the front end shows the page's document in its Elements/tree or console
            text = await tools.eval("document.body.innerText + ' ' + (document.querySelector('body').shadowRoot ? 'shadow' : '')")
            print('front end text:', (text or '')[:120].replace('\n', ' '))
            if not title:
                failures.append('front end has no title')
    finally:
        chrome.terminate()
    print('FAILED: ' + '; '.join(failures) if failures else 'ok')
    return 1 if failures else 0


p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--user', default='admin')
p.add_argument('--password', default='admin')
p.add_argument('--out-dir', default='/tmp/devtools-shots')
p.add_argument('--wait', type=float, default=25)
raise SystemExit(asyncio.run(run(p.parse_args())))
