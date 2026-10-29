#!/usr/bin/env python3
"""
The Display page's real buttons (headless Chrome, real mouse events) against a board: for every orientation and flip the screen
must be turned by the compositor (transform read back from the board, the page of the window left alone, the manager reports
`turned_by_output`) and, when a camera sees the output, the picture must be the one the page produced in the equivalence run.

    .venv/bin/python tools/gfx-bench/rotation/rotation-ui.py --host 192.168.1.173 [--camera /dev/video4 --reference DIR]
"""
import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets
from PIL import Image, ImageChops, ImageStat

import rotlib

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('ui_smoke', os.path.join(HERE, '..', '..', 'ui-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'manager'))
CONFIG = {(0, 0): 'normal', (0, 1): 'flipped', (0, 2): 'flipped-180', (1, 0): '270', (1, 1): 'flipped-270', (1, 2): 'flipped-90',
          (2, 0): '180', (2, 1): 'flipped-180', (2, 2): 'flipped', (3, 0): '90', (3, 1): 'flipped-90', (3, 2): 'flipped-270'}


def grab(camera, path):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'v4l2', '-i', camera, '-frames:v', '4', '-update', '1', path], check=True, timeout=30)
    return Image.open(path).convert('L').resize((96, 54))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--camera')
    ap.add_argument('--reference', default='/tmp/claude-1000/equiv')
    ap.add_argument('--out', default='/tmp/claude-1000/ui-rot')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    failures = 0
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9343', '--no-sandbox', '--ignore-certificate-errors',
                               f'--user-data-dir={tempfile.mkdtemp()}', '--window-size=1400,1000', 'about:blank'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with rotlib.Board(args.host, 'corners') as board:
            for _ in range(50):
                try:
                    page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9343/json/list')) if t['type'] == 'page')
                    break
                except Exception:
                    time.sleep(.3)
            async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
                c = smoke.Cdp(ws)
                asyncio.get_running_loop().create_task(c.pump())
                for d in ('Page', 'Runtime'):
                    await c.call(d + '.enable')
                await c.call('Page.navigate', url=f'https://{args.host}/login')
                await asyncio.sleep(2)
                await c.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams({username:'root',password:'unitotem'})}).then(r=>r.status)", True)
                await c.call('Page.navigate', url=f'https://{args.host}/settings/display')
                await asyncio.sleep(6)
                for r in range(4):
                    for f in range(3):
                        await c.eval("(() => { const g = [...document.querySelectorAll('.v-btn-toggle')]; g[0].querySelectorAll('.v-btn')[%d].click(); g[1].querySelectorAll('.v-btn')[%d].click() })()" % (r, f))
                        await asyncio.sleep(4)
                        transform = [l for l in board.ssh(f'{rotlib.ENV} wlr-randr').splitlines() if 'Transform' in l][0].split(':')[1].strip()
                        info = (await board.api.call('Viewers/list'))['viewers'][board.viewer]['windows'][0]
                        body = board.ssh('python3 /var/lib/unitotem/ticker-test/board-cdp.py eval "document.body.getAttribute(\'r\') + \'/\' + document.querySelector(\'main\').getAttribute(\'f\')" 2>&1 | tail -1')
                        row = {'r': r, 'f': f, 'transform': transform, 'expected': CONFIG[(r, f)], 'reported': [info.get('orientation'), info.get('flip')],
                               'turned_by_output': info.get('turned_by_output'), 'size': [info.get('width'), info.get('height')], 'page_body': body}
                        if args.camera:
                            shot = grab(args.camera, os.path.join(args.out, f'ui-r{r}-f{f}.png'))
                            ref = Image.open(os.path.join(args.reference, f'page-r{r}-f{f}.png')).convert('L').resize((96, 54))
                            row['picture_distance'] = round(ImageStat.Stat(ImageChops.difference(shot, ref)).mean[0], 2)
                        ok = transform == CONFIG[(r, f)] and row['turned_by_output'] and row['reported'] == [r, f] and row.get('picture_distance', 0) < 1
                        failures += not ok
                        print(('ok   ' if ok else 'FAIL ') + json.dumps(row), flush=True)
    finally:
        chrome.terminate()
    print('failures', failures)
    sys.exit(1 if failures else 0)


asyncio.run(main())
