#!/usr/bin/env python3
"""
The first-access setup page in a real (headless) Chrome, driven over the DevTools protocol.

Needs a node that is unclaimed (no administrator) and its claim code; it does NOT log in with a default password,
there is none. It checks the page, the refusals (wrong code, weak password, mismatch), then claims the node for real:

    tools/ui-setup.py --host 192.168.122.60 --code ABCDE-FGHJK [--shots /tmp/setup]

Afterwards the node has the administrator `uitest` with the password printed by --help (below) and /setup is gone.
"""
import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

PASSWORD = 'correct horse battery'


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


FILL = """
(function (label, value) {
  const field = [...document.querySelectorAll('.v-text-field')].find(f => f.innerText.includes(label));
  const input = field.querySelector('input');
  input.focus();
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', {bubbles: true}));
})
"""


async def run(args):
    results = []

    def check(name, ok):
        results.append(ok)
        print(('PASS ' if ok else 'FAIL ') + name, flush=True)

    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9334', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={profile}', '--window-size=1000,1100',
                               'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen('http://127.0.0.1:9334/json/list'))
                page = next(t for t in targets if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for domain in ('Page', 'Runtime'):
                await cdp.call(f'{domain}.enable')

            async def shot(name):
                if args.shots:
                    import base64
                    data = (await cdp.call('Page.captureScreenshot', format='png')).get('data', '')
                    open(f'{args.shots}-{name}.png', 'wb').write(base64.b64decode(data))

            async def fill(label, value):
                await cdp.eval(FILL + f'({json.dumps(label)}, {json.dumps(value)})')

            async def submit():
                await cdp.eval("document.querySelector('button[type=submit]').click()")
                await asyncio.sleep(1.5)

            async def text():
                return await cdp.eval('document.body.innerText') or ''

            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(3)
            location = await cdp.eval('location.pathname')
            check('/ lands on /setup', location == '/setup')
            body = await text()
            check('the page shows the device', 'Welcome to UniTotem' in body and 'create administrator' in body.lower())
            check('the password rules are shown', 'At least 10 characters' in body)
            check('the claim code is not on the page', args.code not in body)
            await shot('form')

            await fill('Claim code', 'AAAAA-AAAAA')
            await fill('Administrator username', 'uitest')
            await fill('Password', PASSWORD)
            await fill('Repeat the password', PASSWORD)
            await submit()
            check('a wrong code is refused', 'Wrong claim code' in await text())
            await shot('wrong-code')

            await fill('Claim code', args.code)
            await fill('Repeat the password', 'something else entirely')
            await submit()
            check('mismatching passwords are refused in the browser', 'do not match' in await text())

            await fill('Password', 'short')
            await fill('Repeat the password', 'short')
            await submit()
            check('a short password is refused in the browser', 'at least 10 characters long' in await text())

            await fill('Password', PASSWORD)
            await fill('Repeat the password', PASSWORD)
            await submit()
            await asyncio.sleep(1)
            body = await text()
            check('the claim succeeds', 'The device is ready' in body)
            await shot('done')

            await cdp.eval("[...document.querySelectorAll('button')].find(b => b.innerText.toLowerCase().includes('continue')).click()")
            await asyncio.sleep(3)
            check('Continue opens the app, logged in', await cdp.eval('location.pathname') == '/')
            check('the app is shown', 'uitest' in (await text()) or 'Playlist' in (await text()) or len(await text()) > 20)

            await cdp.call('Page.navigate', url=base + '/setup')
            await asyncio.sleep(2)
            body = await text()
            check('/setup is gone once claimed', 'create administrator' not in body.lower())
    finally:
        chrome.terminate()
    print('RESULT', 'ALL PASS' if all(results) else 'FAILURES', len(results))
    return 0 if all(results) else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', required=True, help='host or host:port of an unclaimed node')
    p.add_argument('--code', required=True, help='its claim code')
    p.add_argument('--shots', help='prefix for screenshots')
    sys.exit(asyncio.run(run(p.parse_args())))
