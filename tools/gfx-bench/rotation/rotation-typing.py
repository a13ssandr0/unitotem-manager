#!/usr/bin/env python3
"""
Typing on a turned screen, with a finger: a virtual touch screen taps a text field, the on-screen keyboard opens by itself, and the keys
are tapped one by one (their places read from the keyboard's own DOM); the field must end up with the text.

    .venv/bin/python tools/gfx-bench/rotation/rotation-typing.py --host 192.168.1.173 [--turns 0,1,3] [--text ciao]
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys

import rotlib

vr = rotlib.vr
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from importlib import import_module  # noqa: E402

PLACEMENTS = [lambda u, v: (u, v), lambda u, v: (1 - u, v), lambda u, v: (u, 1 - v), lambda u, v: (1 - u, 1 - v),
              lambda u, v: (v, u), lambda u, v: (1 - v, u), lambda u, v: (v, 1 - u), lambda u, v: (1 - v, 1 - u)]
FORWARD = {0: lambda u, v: (u, v), 1: lambda u, v: (1 - v, u), 2: lambda u, v: (1 - u, 1 - v), 3: lambda u, v: (v, 1 - u)}


def glass_for(r, f, u, v):
    """The point of the glass (0..1) that shows content point (u, v) when the screen is turned r quarter turns and mirrored f."""
    # brute force: find the glass point g such that content(g) == (u, v), using the forward map used by rotation-touch
    best = None
    for place in PLACEMENTS:
        gu, gv = place(u, v)
        mu, mv = (1 - u if f == 1 else u), (1 - v if f == 2 else v)
        fu, fv = FORWARD[r](mu, mv)
        if abs(fu - gu) < 1e-9 and abs(fv - gv) < 1e-9:
            best = (gu, gv)
    return best if best else FORWARD[r](1 - u if f == 1 else u, 1 - v if f == 2 else v)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--turns', default='0,1,3')
    ap.add_argument('--text', default='ciao')
    args = ap.parse_args()
    host, failures = args.host, 0
    async with rotlib.Board(host, 'field') as b:
        scp = vr.SSH + ['scp', '-O'] + vr.OPTS
        subprocess.run(scp + [os.path.join(HERE, 'touchdev.py'), f'root@{host}:{rotlib.REMOTE}/touchdev.py'], capture_output=True, check=True)
        subprocess.run(scp + [os.path.join(HERE, '..', '..', 'ticker', 'board-cdp.py'), f'root@{host}:{rotlib.REMOTE}/board-cdp.py'], capture_output=True, check=True)
        await b.api.send('Viewers/set_keyboard', viewer_id=b.viewer, window_id=b.win, enabled=True, button_position='bottom-right', layout='english')
        b.ssh(f'nohup python3 {rotlib.REMOTE}/touchdev.py >/dev/null 2>&1 & echo $! > {rotlib.REMOTE}/touchpid; sleep 3')
        key = None
        try:
            devices = await b.api.call('Settings/Display/Touch/devices')
            key = next(d['key'] for d in devices['devices'] if d.get('name') == 'UniTotem Test Touch')
            await b.api.call('Settings/Display/Touch/set_output', reply='Settings/Display/Touch/devices', key=key, output=b.output)
            await asyncio.sleep(2)

            def tap(r, f, u, v):
                gx, gy = glass_for(r, f, u, v)
                b.ssh(f'echo "{gx:.4f} {gy:.4f}" > /run/rot-touch.fifo; sleep 0.5')

            for r in [int(x) for x in args.turns.split(',')]:
                await b.set_content(r, 0)
                await asyncio.sleep(6)
                b.ssh('curl -s "localhost:8099/set?w=field" >/dev/null')
                await asyncio.sleep(4)
                b.ssh(f'python3 {rotlib.REMOTE}/board-cdp.py eval "window.utKeyboard && window.utKeyboard.focusChanged(false)" 0')
                n0 = len(json.loads(b.ssh('curl -s localhost:8099/vals') or '[]'))
                tap(r, 0, 0.5, 0.14)                # the field
                await asyncio.sleep(3)
                opened = b.ssh(f"python3 {rotlib.REMOTE}/board-cdp.py eval 'getComputedStyle(document.getElementById(\"ut-kbd-panel\")).display' 0 2>&1").strip('"')
                keys = {}
                for ch in sorted(set(args.text)):
                    js = ('(()=>{const e=[...document.querySelectorAll(".hg-button")].find(k=>k.dataset.skbtn==="%s");if(!e)return null;'
                          'const r=e.getBoundingClientRect();return JSON.stringify([(r.x+r.width/2)/innerWidth,(r.y+r.height/2)/innerHeight])})()') % ch
                    raw = b.ssh(f"python3 {rotlib.REMOTE}/board-cdp.py eval '{js}' 0 2>&1")
                    try:
                        keys[ch] = json.loads(json.loads(raw))
                    except (ValueError, TypeError):
                        print('key', ch, 'raw:', raw[:160], flush=True)
                        keys[ch] = None
                if opened != 'flex' or None in keys.values():
                    print('FAIL', json.dumps({'r': r, 'keyboard': opened, 'keys': keys}), flush=True)
                    failures += 1
                    continue
                for ch in args.text:
                    tap(r, 0, *keys[ch])
                await asyncio.sleep(1.5)
                vals = json.loads(b.ssh('curl -s localhost:8099/vals') or '[]')[n0:]
                ok = len(vals) == len(args.text) and vals[-1].endswith(args.text)       # the field keeps what earlier rounds typed
                failures += not ok
                print(('ok   ' if ok else 'FAIL ') + json.dumps({'r': r, 'keyboard': opened, 'typed': vals[-1] if vals else None, 'wanted': args.text}), flush=True)
        finally:
            b.ssh(f'echo quit > /run/rot-touch.fifo; sleep 1; kill $(cat {rotlib.REMOTE}/touchpid) 2>/dev/null; true')
            if key:
                await b.api.send('Settings/Display/Touch/forget', key=key)
            b.ssh(f'python3 {rotlib.REMOTE}/board-cdp.py eval "window.utKeyboard && window.utKeyboard.focusChanged(false)" 0')
    print('failures', failures)
    sys.exit(1 if failures else 0)


asyncio.run(main())
