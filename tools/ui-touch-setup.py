#!/usr/bin/env python3
"""
The "Touch doesn't match? Fix it" dialog of the Display page, driven in a real (headless) Chrome while a virtual touch panel on the board taps the
targets: the choices, a step, the question after the taps, and "Yes, keep it". Screenshots go to --out-dir.

    tools/ui-touch-setup.py --host 192.168.0.254 --user root --password unitotem --out-dir /tmp/touch-ui
"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

PORT = 9347
HERE = Path(__file__).resolve().parent
SSH = ['sshpass', '-p', 'unitotem']
OPTS = ['-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=no',
        '-o', 'LogLevel=ERROR']
ENV = {**os.environ, 'SSH_ASKPASS_REQUIRE': 'never'}
ENV.pop('DISPLAY', None)
ENV.pop('SSH_ASKPASS', None)


def board(host, command, timeout=60):
    return subprocess.run(SSH + ['ssh'] + OPTS + [f'root@{host}', command], capture_output=True, text=True, timeout=timeout, env=ENV).stdout.strip()


class Cdp:
    def __init__(self, ws):
        self.ws, self.n, self.pending = ws, 0, {}

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg:
                self.pending.pop(msg['id']).set_result(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await fut).get('result', {})

    async def eval(self, expression, await_promise=False):
        return (await self.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=await_promise)).get('result', {}).get('value')


async def shot(cdp, path):
    Path(path).write_bytes(base64.b64decode((await cdp.call('Page.captureScreenshot', format='png'))['data']))


CLICK = """(text) => { const els = [...document.querySelectorAll('button, .v-card')].filter(e => e.innerText && e.innerText.trim().toLowerCase().startsWith(text.toLowerCase()));
  const el = els.find(e => e.tagName === 'BUTTON') || els[els.length - 1]; if (el) el.click(); return !!el; }"""


async def main(args):
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    host, failures = args.host, []
    subprocess.run(SSH + ['scp', '-O'] + OPTS + [str(HERE / 'gfx-bench/rotation/touchdev.py'), f'root@{host}:/var/lib/unitotem/touchdev.py'], capture_output=True, env=ENV)
    board(host, 'cd /var/lib/unitotem && (SINGLE_TOUCH=0 nohup python3 touchdev.py >/dev/null 2>&1 & echo $! > touchdev.pid); sleep 3; ls /run/rot-touch.fifo')
    chrome = subprocess.Popen(['google-chrome', '--headless=new', f'--remote-debugging-port={PORT}', '--no-sandbox', '--ignore-certificate-errors',
                               f'--user-data-dir={tempfile.mkdtemp()}', '--window-size=1100,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        ws = await websockets.connect(page['webSocketDebuggerUrl'], max_size=None)
        cdp = Cdp(ws)
        asyncio.get_running_loop().create_task(cdp.pump())
        await cdp.call('Page.enable')
        await cdp.call('Runtime.enable')
        base = f'https://{host}'
        await cdp.call('Page.navigate', url=base + '/login')
        await asyncio.sleep(2)
        await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                       f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
        await cdp.call('Page.navigate', url=base + '/settings/display')
        await asyncio.sleep(7)
        await shot(cdp, out / '1-card.png')
        # the virtual panel's own row
        opened = await cdp.eval("""(() => { const row = [...document.querySelectorAll('.v-list-item')].find(r => r.innerText.includes('UniTotem Test'));
          const b = row && [...row.querySelectorAll('button')].find(b => b.innerText.toLowerCase().includes('fix it')); if (b) b.click(); return !!b; })()""")
        print('dialog opened:', opened)
        if not opened:
            failures.append('no row for the virtual panel')
        await asyncio.sleep(1.5)
        await shot(cdp, out / '2-choices.png')
        await cdp.eval(f"({CLICK})('Start')")
        await asyncio.sleep(2)
        await shot(cdp, out / '3-step.png')
        step = await cdp.eval("document.querySelector('.v-dialog')?.innerText || ''")
        print('step text:', ' / '.join(step.split('\n')[:4]))
        if 'Step 1 of 4' not in step:
            failures.append('the step view did not show Step 1 of 4')
        for point in ((0.12, 0.12), (0.88, 0.12), (0.88, 0.88), (0.12, 0.88)):
            board(host, f'echo "{point[0]} {point[1]}" > /run/rot-touch.fifo; sleep 0.8')
        await asyncio.sleep(2)
        await shot(cdp, out / '4-try.png')
        question = await cdp.eval("document.querySelector('.v-dialog')?.innerText || ''")
        print('after the taps:', ' / '.join(question.split('\n')[:3]))
        if 'Does the dot follow your finger' not in question:
            failures.append('the question did not show')
        await cdp.eval(f"({CLICK})('Yes, keep it')")
        await asyncio.sleep(3)
        await shot(cdp, out / '5-saved.png')
        row = await cdp.eval("[...document.querySelectorAll('.v-list-item')].find(r => r.innerText.includes('UniTotem Test'))?.innerText || ''")
        print('row now:', ' '.join(row.split()))
        gone = await cdp.eval("!document.querySelector('.v-dialog')")
        if not gone:
            failures.append('the dialog stayed open after saving')
    finally:
        chrome.terminate()
        board(host, 'echo quit > /run/rot-touch.fifo; sleep 1; kill $(cat /var/lib/unitotem/touchdev.pid) 2>/dev/null; true')
    print('FAILED: ' + '; '.join(failures) if failures else 'ok')
    return 1 if failures else 0


p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--user', default='admin')
p.add_argument('--password', default='admin')
p.add_argument('--out-dir', default='/tmp/touch-ui')
sys.exit(asyncio.run(main(p.parse_args())))
