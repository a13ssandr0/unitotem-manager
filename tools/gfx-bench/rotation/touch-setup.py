#!/usr/bin/env python3
"""
The guided setup of a touch panel, end to end on a board: a virtual panel (touchdev.py) that sits on its screen turned and mirrored every way is set
up through the node's own API (corners, then exact points), the screen is turned and mirrored every way too, and what the setup concluded is checked by
a real tap through the compositor: after "Yes, keep it" a tap on the glass at the place that belongs to a point of the picture must reach the page at that
point. Screenshots of the totem's veil (running and trying) are saved next to the results.

    .venv/bin/python tools/gfx-bench/rotation/touch-setup.py --host 192.168.0.254 [--out /tmp/touch-setup]
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
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'manager'))
from plugins.unitotem_node.system import touch, touch_calibration as tc       # noqa: E402  the same maths the node runs

VERIFY = (0.3, 0.7)             # a point of the picture, checked after the setup
CASES = [                       # (output transform, panel turn/flip against its screen, method)
    ('normal', (0, 0), 'corners'), ('normal', (1, 0), 'corners'), ('90', (0, 0), 'corners'), ('90', (3, 1), 'corners'),
    ('flipped-270', (2, 0), 'corners'), ('180', (1, 2), 'corners'), ('normal', (0, 0), 'points'), ('270', (1, 0), 'points'),
]
SCALED = (0.92, 0.0, 0.03, 0.0, 1.06, -0.03)        # a frame whose glass is a bit smaller and shifted: only the exact points can tell


def raw_of(panel, transform, point):
    """Where on the glass (the panel's raw frame, 0..1) the picture's `point` is, for a panel with this matrix under that output transform."""
    native = tc.apply(tc.invert(tc.output_matrix(transform)), point)
    return tc.apply(tc.invert(panel), native)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--out', default='/tmp/touch-setup')
    ap.add_argument('--only', type=int, nargs='*', help='run only these cases (their numbers in CASES)')
    ap.add_argument('--fragment', action='store_true', help='print the compositor configuration the node wrote after each setup')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    host, failures = args.host, 0
    async with rotlib.Board(host, 'corners') as board:
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, 'touchdev.py'), f'root@{host}:{rotlib.REMOTE}/touchdev.py'], capture_output=True, check=True)
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, '..', '..', 'ticker', 'board-cdp.py'), f'root@{host}:{rotlib.REMOTE}/board-cdp.py'],
                       capture_output=True, check=True)
        board.ssh(f'SINGLE_TOUCH=0 nohup python3 {rotlib.REMOTE}/touchdev.py >/dev/null 2>&1 & echo $! > {rotlib.REMOTE}/touchpid; sleep 3; ls /run/rot-touch.fifo')
        api = board.api
        try:
            devices = await api.call('Settings/Display/Touch/devices')
            key = next(d['key'] for d in devices['devices'] if d.get('name', '').startswith('UniTotem Test'))
            await api.call('Settings/Display/Touch/set_output', reply='Settings/Display/Touch/devices', key=key, output=board.output)
            await asyncio.sleep(2)
            board.ssh(f'python3 {rotlib.REMOTE}/board-cdp.py eval "window.utKeyboard && window.utKeyboard.focusChanged(false)" 0')

            def tap(point):
                board.ssh(f'echo "{point[0]:.5f} {point[1]:.5f}" > /run/rot-touch.fifo; sleep 0.7')

            async def state():
                try:
                    return await api.call('Settings/Display/Touch/setup')
                except TimeoutError:
                    print('no answer; error:', [f.get('extra', '')[-900:] for f in api.frames[-40:] if f.get('target') == 'error'], flush=True)
                    raise

            for number, (transform, pair, method) in enumerate(CASES):
                if args.only is not None and number not in args.only:
                    continue
                panel = touch.CALIBRATION[pair] if method == 'corners' else SCALED
                if method == 'points' and pair != (0, 0):
                    panel = tuple(round(v, 6) for v in _compose(touch.CALIBRATION[pair], SCALED))
                board.set_transform(transform)
                await asyncio.sleep(3)
                await api.call('Settings/Display/Touch/reset', reply='Settings/Display/Touch/devices', key=key)
                await asyncio.sleep(1.5)
                await api.send('Settings/Display/Touch/start_setup', key=key, method=method)
                await asyncio.sleep(1.5)
                begin = await state()
                targets = tc.CORNER_TARGETS if method == 'corners' else tc.POINT_TARGETS
                ok = begin['phase'] == 'running' and begin['total'] == len(targets)
                for index, target in enumerate(targets):
                    if index == 1 and number in (0, 6):
                        board.ssh(f'python3 {rotlib.REMOTE}/board-cdp.py shot {rotlib.REMOTE}/running-{method}.png 0')
                    tap(raw_of(panel, transform, target))
                mid = await state()
                for _ in range(6):
                    if mid['phase'] != 'running':
                        break
                    await asyncio.sleep(0.5)
                    mid = await state()
                if number == 0:
                    board.ssh(f'python3 {rotlib.REMOTE}/board-cdp.py shot {rotlib.REMOTE}/trying.png 0')
                ok = ok and mid['phase'] == 'try'
                await api.send('Settings/Display/Touch/confirm_setup')
                await asyncio.sleep(2.5)
                snapshot = await api.call('Settings/Display/Touch/devices')
                dev = next(d for d in snapshot['devices'] if d['key'] == key)
                if args.fragment:
                    print(board.ssh('cat /run/unitotem/labwc/node/labwc/rc.xml 2>/dev/null || find /run/unitotem/labwc -type f | head'), flush=True)
                # the proof: a tap on the glass where the picture's point is must reach the page at that point
                before = len(json.loads(board.ssh('curl -s localhost:8099/clicks') or '[]'))
                tap(raw_of(panel, transform, VERIFY))
                clicks = json.loads(board.ssh('curl -s localhost:8099/clicks') or '[]')[before:]
                got = (clicks[-1]['x'], clicks[-1]['y']) if clicks else None
                landed = got is not None and abs(got[0] - VERIFY[0]) < 0.04 and abs(got[1] - VERIFY[1]) < 0.04
                ok = ok and landed
                failures += not ok
                print(('ok   ' if ok else 'FAIL ') + json.dumps({'transform': transform, 'panel': pair if method == 'corners' else 'scaled', 'method': method,
                      'stored': {'turn': dev.get('turn'), 'flip': dev.get('flip'), 'calibrated': dev.get('calibrated')},
                      'picture_point': VERIFY, 'page_received': got, 'begin': begin['phase'], 'total': begin.get('total'), 'after_taps': mid['phase']}), flush=True)
            for name in ('running-corners', 'running-points', 'trying'):
                subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [f'root@{host}:{rotlib.REMOTE}/{name}.png', os.path.join(args.out, f'{name}.png')], capture_output=True)
        finally:
            try:
                await api.send('Settings/Display/Touch/cancel_setup')
                await api.send('Settings/Display/Touch/forget', key=key)
            except Exception:
                pass
            board.ssh(f'echo quit > /run/rot-touch.fifo; sleep 1; kill $(cat {rotlib.REMOTE}/touchpid) 2>/dev/null; true')
    print('failures', failures)
    sys.exit(1 if failures else 0)


def _compose(outer, inner):
    """outer(inner(p)) as one matrix."""
    a, b, c, d, e, f = inner
    A, B, C, D, E, F = outer
    return (A * a + B * d, A * b + B * e, A * c + B * f + C, D * a + E * d, D * b + E * e, D * c + E * f + F)


asyncio.run(main())
