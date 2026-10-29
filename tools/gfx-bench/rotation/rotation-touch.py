#!/usr/bin/env python3
"""
Does a finger land where the picture is when the compositor turns the screen? A virtual touch screen (touchdev.py) is mapped to the
output with the node's own API and taps a point in ITS frame (the glass: what a real panel reports whatever the screen shows);
the corners page says where the tap arrived in its own content space. For a screen turned by the page the content point is
the inverse of the page's turn/mirror; the compositor must give the same.

    .venv/bin/python tools/gfx-bench/rotation/rotation-touch.py --host 192.168.1.173
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
NX, NY = 0.2, 0.3


def content_point(r, f, nx, ny):
    """Where a point of the glass (0..1, native frame) is in the content: the page shows content (u, v) at native
    r=0 (u, v), r=1 (1-v, u), r=2 (1-u, 1-v), r=3 (v, 1-u); flip 1 mirrors u first, flip 2 mirrors v first."""
    forward = {0: lambda u, v: (u, v), 1: lambda u, v: (1 - v, u), 2: lambda u, v: (1 - u, 1 - v), 3: lambda u, v: (v, 1 - u)}[r]
    best = None
    # a signed permutation of the unit square: the content point is one of the eight placements of (nx, ny)
    for cu, cv in ((nx, ny), (1 - nx, ny), (nx, 1 - ny), (1 - nx, 1 - ny), (ny, nx), (1 - ny, nx), (ny, 1 - nx), (1 - ny, 1 - nx)):
        mu, mv = (1 - cu if f == 1 else cu), (1 - cv if f == 2 else cv)
        fu, fv = forward(mu, mv)
        if abs(fu - nx) < 1e-9 and abs(fv - ny) < 1e-9:
            best = (cu, cv)
            break
    return best


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    args = ap.parse_args()
    host = args.host
    failures = 0
    async with rotlib.Board(host, 'corners') as board:
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, 'touchdev.py'), f'root@{host}:{rotlib.REMOTE}/touchdev.py'], capture_output=True, check=True)
        board.ssh(f'nohup python3 {rotlib.REMOTE}/touchdev.py >/dev/null 2>&1 & echo $! > {rotlib.REMOTE}/touchpid; sleep 3; ls /run/rot-touch.fifo')
        try:
            devices = (await board.api.call('Settings/Display/Touch/devices'))
            key = next(d['key'] for d in devices['devices'] if d.get('name') == 'UniTotem Test Touch')
            await board.api.call('Settings/Display/Touch/set_output', reply='Settings/Display/Touch/devices', key=key, output=board.output)
            await asyncio.sleep(2)
            for r in range(4):
                for f in range(3):
                    await board.set_content(r, f)
                    await asyncio.sleep(5)
                    before = len(json.loads(board.ssh('curl -s localhost:8099/clicks') or '[]'))
                    board.ssh(f'echo "{NX} {NY}" > /run/rot-touch.fifo; sleep 1')
                    clicks = json.loads(board.ssh('curl -s localhost:8099/clicks') or '[]')[before:]
                    want = content_point(r, f, NX, NY)
                    got = (clicks[-1]['x'], clicks[-1]['y']) if clicks else None
                    ok = got is not None and abs(got[0] - want[0]) < 0.03 and abs(got[1] - want[1]) < 0.03
                    failures += not ok
                    print(('ok   ' if ok else 'FAIL ') + json.dumps({'r': r, 'f': f, 'tap_on_glass': [NX, NY], 'expected_in_content': [round(v, 3) for v in want], 'page_received': got}), flush=True)
        finally:
            board.ssh(f'echo quit > /run/rot-touch.fifo; sleep 1; kill $(cat {rotlib.REMOTE}/touchpid) 2>/dev/null; true')
            try:
                await board.api.send('Settings/Display/Touch/forget', key=key)
            except Exception:
                pass
    print('failures', failures)
    sys.exit(1 if failures else 0)


asyncio.run(main())
