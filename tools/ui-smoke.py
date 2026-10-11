#!/usr/bin/env python3
"""
Smoke test of the web UI in a real (headless) Chrome, driven over the DevTools protocol.

Logs in, opens every page, and records for each one the console errors, the uncaught exceptions and the
visible text. Meant to be run before and after touching the web UI and then compared, since there is no
other automated test of the front end:

    tools/ui-smoke.py --host 192.168.122.50 --out /tmp/before.json
    ... change something ...
    tools/ui-smoke.py --host 192.168.122.50 --out /tmp/after.json --compare /tmp/before.json

Digits are masked in the comparison, so clocks and counters do not show up as differences.
"""
import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

PAGES = ['/', '/settings/audio', '/settings/display', '/settings/remote', '/settings/users', '/settings/timers',
         '/settings/network', '/settings/updates', '/settings/backup', '/viewers', '/info', '/does-not-exist']


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


async def run(args):
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9333', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={profile}', '--window-size=1400,1000',
                               'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen('http://127.0.0.1:9333/json/list'))
                page = next(t for t in targets if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for domain in ('Page', 'Runtime', 'Log'):
                await cdp.call(f'{domain}.enable')
            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            ok = await cdp.eval(
                "fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            if ok not in (200, 204):
                sys.exit(f'login failed: {ok}')
            result = {}
            for path in PAGES:
                cdp.events.clear()
                await cdp.call('Page.navigate', url=base + path)
                await asyncio.sleep(args.wait)
                errors = []
                for ev in cdp.events:
                    if ev['method'] == 'Runtime.exceptionThrown':
                        d = ev['params']['exceptionDetails']
                        errors.append('exception: ' + (d.get('exception', {}).get('description') or d['text'])[:200])
                    elif ev['method'] == 'Runtime.consoleAPICalled' and ev['params']['type'] in ('error', 'warning'):
                        errors.append(ev['params']['type'] + ': ' + ' '.join(
                            str(a.get('value', a.get('description', ''))) for a in ev['params']['args'])[:200])
                text = await cdp.eval('document.body.innerText') or ''
                result[path] = {'errors': errors, 'text': text[:4000]}
                print(f"{path:22} errors={len(errors)} text={len(text)}")
            return result
    finally:
        chrome.terminate()


def mask(text):
    return re.sub(r'\d+', '#', text)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', required=True)
    p.add_argument('--user', default='admin')
    p.add_argument('--password', default='admin')
    p.add_argument('--out')
    p.add_argument('--compare')
    p.add_argument('--wait', type=float, default=4)
    args = p.parse_args()
    result = asyncio.run(run(args))
    if args.out:
        json.dump(result, open(args.out, 'w'), indent=1)
    if args.compare:
        before = json.load(open(args.compare))
        bad = 0
        for path, now in result.items():
            old = before.get(path)
            if old is None:
                continue
            if mask(old['text']) != mask(now['text']):
                bad += 1
                print(f'DIFFERENT TEXT: {path}')
            if now['errors'] and now['errors'] != old['errors']:
                bad += 1
                print(f'NEW ERRORS: {path}: {now["errors"][:3]}')
        print('no differences' if not bad else f'{bad} difference(s)')
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
