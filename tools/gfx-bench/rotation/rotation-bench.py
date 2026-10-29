#!/usr/bin/env python3
"""
Compares rotating the CONTENT in the page (body[r]) with rotating the OUTPUT in the compositor (wlr-randr --transform),
on a real board. Run from the dev PC (needs the repository's .venv for websockets/httpx).

    .venv/bin/python tools/gfx-bench/rotation/rotation-bench.py --host 192.168.1.173 --out DIR [--seconds 30]
        [--configs A,B1,C90,C270] [--workloads idle,video,...]

It copies rotsrv.py / rotmeasure.py / the test videos to /var/lib/unitotem/rot on the board, puts the test page in a temporary
playlist assigned to the first window, then for every configuration sets the window's content orientation (through the
manager's API, like the web UI) and the output transform, and measures every workload. Everything is put back at the end
(orientation, transform, assignment); a `sleep N; wlr-randr --transform normal` safety net runs on the board meanwhile.
Rows are appended to DIR/<host>.jsonl.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('venus_run', os.path.join(HERE, '..', '..', 'ticker', 'venus-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

REMOTE = '/var/lib/unitotem/rot'
ENV = 'export XDG_RUNTIME_DIR=/run/unitotem/xdg WAYLAND_DISPLAY=wayland-0;'
# name: (content orientation, content flip, output transform)
CONFIGS = {
    'A': (0, 0, 'normal'),          # baseline
    'B1': (1, 0, 'normal'),         # page turns the content a quarter clockwise
    'B3': (3, 0, 'normal'),
    'C90': (0, 0, '90'),            # compositor turns the output
    'C270': (0, 0, '270'),
    'B2': (2, 0, 'normal'),
    'C180': (0, 0, '180'),
    'BF': (0, 1, 'normal'),         # page mirrors
    'CF': (0, 0, 'flipped'),        # compositor mirrors
}
WORKLOADS = ['idle', 'video', 'video4', 'canvas', 'webgl', 'webgl_mid', 'webgl_heavy', 'crawl']


def sh(host, cmd, timeout=300):
    return vr.ssh(host, cmd, timeout)


def scp_to(host, local, remote):
    import subprocess
    subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [local, f'root@{host}:{remote}'], capture_output=True, check=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seconds', type=int, default=30)
    ap.add_argument('--configs', default='A,B1,C90,C270')
    ap.add_argument('--workloads', default=','.join(WORKLOADS))
    ap.add_argument('--repeat', type=int, default=1, help='run the whole workload list this many times per configuration (every run reloads the page)')
    ap.add_argument('--videos', default='/tmp/claude-1000/rotvideos')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    host = args.host
    workloads = args.workloads.split(',')

    out = sh(host, f'{ENV} wlr-randr --json')
    output = json.loads(out)[0]['name']
    sh(host, f'mkdir -p {REMOTE}')
    for f in ('rotsrv.py', 'rotmeasure.py'):
        scp_to(host, os.path.join(HERE, f), f'{REMOTE}/{f}')
    for v in ('v1080.mp4',) + (('v2160.mp4',) if 'video4k' in workloads else ()):
        scp_to(host, os.path.join(args.videos, v), f'{REMOTE}/{v}')
    sh(host, f'kill $(cat {REMOTE}/pid) 2>/dev/null; nohup python3 {REMOTE}/rotsrv.py {REMOTE} >/dev/null 2>&1 & echo $! > {REMOTE}/pid; sleep 2; curl -s localhost:8099/workload')
    net = sh(host, f'{ENV} (sleep 3000; wlr-randr --output {output} --transform normal) >/dev/null 2>&1 & echo $!')

    async with vr.Api(host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer = next(iter(views))
        window = views[viewer]['windows'][0]
        win = window['window_id']
        before = dict(orientation=window.get('orientation', 0), flip=window.get('flip', 0), playlist=window.get('assigned_playlist'))
        reply = await api.call('Scheduler/create_playlist', reply='Scheduler/playlists', name='rotation-bench')
        playlist = next(p['playlist_id'] for p in reply['playlists'] if p['name'] == 'rotation-bench')
        await api.send('Scheduler/add_url_assets', items=[{'url': 'http://127.0.0.1:8099/run.html', 'name': 'rotation-bench', 'duration': 3600, 'media_type': 0}], playlist_id=playlist)
        await asyncio.sleep(2)
        items = (await api.call('Scheduler/assets', playlist_id=playlist)).get('items', [])
        await api.send('Scheduler/edit_asset', uuid=items[0]['uuid'], playlist_id=playlist, enabled=True, duration=3600)
        await asyncio.sleep(1)
        await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=win, playlist_id=playlist)
        await asyncio.sleep(6)
        try:
            with open(os.path.join(args.out, f'{host}.jsonl'), 'a') as log:
                for cname in args.configs.split(','):
                    r, f, transform = CONFIGS[cname]
                    await api.send('Settings/Display/set_orientation', viewer_id=viewer, window_id=win, orientation=r)
                    await api.send('Settings/Display/set_flip', viewer_id=viewer, window_id=win, flip=f)
                    sh(host, f'{ENV} wlr-randr --output {output} --transform {transform}')
                    await asyncio.sleep(5)
                    for w in workloads * args.repeat:
                        sh(host, f'curl -s "localhost:8099/set?w={w}"')
                        await asyncio.sleep(10)
                        raw = sh(host, f'python3 {REMOTE}/rotmeasure.py {args.seconds}', timeout=args.seconds + 60)
                        try:
                            row = {'host': host, 'config': cname, 'workload': w, **json.loads(raw)}
                        except ValueError:
                            row = {'host': host, 'config': cname, 'workload': w, 'error': raw[:200]}
                        print(json.dumps(row), flush=True)
                        log.write(json.dumps(row) + '\n')
                        log.flush()
        finally:
            sh(host, f'{ENV} wlr-randr --output {output} --transform normal')
            await api.send('Settings/Display/set_orientation', viewer_id=viewer, window_id=win, orientation=before['orientation'])
            await api.send('Settings/Display/set_flip', viewer_id=viewer, window_id=win, flip=before['flip'])
            if before['playlist']:
                await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=win, playlist_id=before['playlist'])
            else:
                await api.send('Viewers/unassign_playlist', viewer_id=viewer, window_id=win)
            await asyncio.sleep(2)
            await api.send('Scheduler/delete_playlist', playlist_id=playlist)
            await asyncio.sleep(1)
            sh(host, f'pkill -P {net}; kill {net} $(cat {REMOTE}/pid); true')


asyncio.run(main())
