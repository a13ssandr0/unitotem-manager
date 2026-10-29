#!/usr/bin/env python3
"""
What the web UI's "Watch the screen" window shows, and where a click in it lands, for each way of turning a screen.
Run from the dev PC (repository .venv, google-chrome):

    .venv/bin/python tools/gfx-bench/rotation/rotation-vnc.py --host 192.168.0.254 --out DIR [--configs A,B3,C90,C270,C180,CF]

For every configuration (names as in rotation-bench.py, plus U1/U2/U3/UF/UF2/U1F: the saved setting only, turned by the viewer) it puts rotsrv.py's `corners` page in the window, opens the screen
window in a headless Chrome, saves a screenshot, switches Control on, clicks the middle of the top-left square of the picture
it shows and asks the board where the page received the click (fractions of the page, 0,0 = its top left).
Everything is put back at the end, like rotation-bench.py.
"""
import argparse
import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = load('venus_run', '..', '..', 'ticker', 'venus-run.py')
smoke = load('ui_smoke', '..', '..', 'ui-smoke.py')
CONFIGS = {'A': (0, 0, 'normal'), 'B1': (1, 0, 'normal'), 'B3': (3, 0, 'normal'), 'C90': (0, 0, '90'), 'C270': (0, 0, '270'),
           'C180': (0, 0, '180'), 'CF': (0, 0, 'flipped'), 'BF': (0, 1, 'normal'),
           # the setting itself (a viewer that turns screens by the compositor does it): no transform is set here
           'U1': (1, 0, None), 'U2': (2, 0, None), 'U3': (3, 0, None), 'UF': (0, 1, None), 'UF2': (0, 2, None), 'U1F': (1, 1, None)}
REMOTE = '/var/lib/unitotem/rot'
ENV = 'export XDG_RUNTIME_DIR=/run/unitotem/xdg WAYLAND_DISPLAY=wayland-0;'
BUTTON = "document.querySelector('button[title=\"Watch the screen\"]')"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--configs', default='A,B3,C90,C270,C180,CF')
    ap.add_argument('--capture', help='video device of an HDMI capture card wired to the board: also saves what the panel really gets')
    args = ap.parse_args()
    host = args.host
    os.makedirs(args.out, exist_ok=True)
    output = json.loads(vr.ssh(host, f'{ENV} wlr-randr --json'))[0]['name']
    vr.ssh(host, f'mkdir -p {REMOTE}')
    for f in ('rotsrv.py',):
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, f), f'root@{host}:{REMOTE}/{f}'], capture_output=True, check=True)
    vr.ssh(host, f'kill $(cat {REMOTE}/pid) 2>/dev/null; nohup python3 {REMOTE}/rotsrv.py {REMOTE} >/dev/null 2>&1 & echo $! > {REMOTE}/pid; sleep 2; curl -s "localhost:8099/set?w=corners"')
    net = vr.ssh(host, f'{ENV} (sleep 3000; wlr-randr --output {output} --transform normal) >/dev/null 2>&1 & echo $!')
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9341', '--no-sandbox', '--ignore-certificate-errors',
                               f'--user-data-dir={tempfile.mkdtemp()}', '--window-size=1400,1000', 'about:blank'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    async with vr.Api(host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer = next(iter(views))
        window = views[viewer]['windows'][0]
        win = window['window_id']
        before = dict(orientation=window.get('orientation', 0), flip=window.get('flip', 0), playlist=window.get('assigned_playlist'))
        reply = await api.call('Scheduler/create_playlist', reply='Scheduler/playlists', name='rotation-vnc')
        playlist = next(p['playlist_id'] for p in reply['playlists'] if p['name'] == 'rotation-vnc')
        await api.send('Scheduler/add_url_assets', items=[{'url': 'http://127.0.0.1:8099/run.html', 'name': 'rotation-vnc', 'duration': 3600, 'media_type': 0}], playlist_id=playlist)
        await asyncio.sleep(2)
        items = (await api.call('Scheduler/assets', playlist_id=playlist)).get('items', [])
        await api.send('Scheduler/edit_asset', uuid=items[0]['uuid'], playlist_id=playlist, enabled=True, duration=3600)
        await asyncio.sleep(1)
        await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=win, playlist_id=playlist)
        await asyncio.sleep(6)
        try:
            for _ in range(50):
                try:
                    page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9341/json/list')) if t['type'] == 'page')
                    break
                except Exception:
                    time.sleep(.3)
            async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
                c = smoke.Cdp(ws)
                asyncio.get_running_loop().create_task(c.pump())
                for d in ('Page', 'Runtime'):
                    await c.call(d + '.enable')
                await c.call('Page.navigate', url=f'https://{host}/login')
                await asyncio.sleep(2)
                await c.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams({username:'root',password:'unitotem'})}).then(r=>r.status)", True)
                await c.call('Page.navigate', url=f'https://{host}/')
                await asyncio.sleep(5)
                for name in args.configs.split(','):
                    r, f, transform = CONFIGS[name]
                    await api.send('Settings/Display/set_orientation', viewer_id=viewer, window_id=win, orientation=r)
                    await api.send('Settings/Display/set_flip', viewer_id=viewer, window_id=win, flip=f)
                    if transform:
                        vr.ssh(host, f'{ENV} wlr-randr --output {output} --transform {transform}')
                    await asyncio.sleep(5)
                    if args.capture:
                        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'v4l2', '-i', args.capture, '-frames:v', '5', '-update', '1',
                                        os.path.join(args.out, f'{host}-{name}-panel.png')], timeout=30)
                    await c.eval(f'{BUTTON}.click()')      # open (a window still open from the previous round is closed first)
                    await asyncio.sleep(1)
                    await c.eval(f'{BUTTON}.click()')
                    await asyncio.sleep(1)
                    if not await c.eval("!!document.querySelector('.vnc-screen canvas')"):
                        await c.eval(f'{BUTTON}.click()')
                    await asyncio.sleep(6)
                    shot = await c.call('Page.captureScreenshot')
                    open(os.path.join(args.out, f'{host}-{name}-view.png'), 'wb').write(base64.b64decode(shot['data']))
                    await c.eval("document.querySelector('.vnc-control').click()")
                    await asyncio.sleep(6)
                    box = await c.eval("(()=>{const b=document.querySelector('.vnc-screen canvas').getBoundingClientRect();return [b.left,b.top,b.width,b.height]})()")
                    x, y = box[0] + box[2] * 0.09, box[1] + box[3] * 0.09     # the middle of the top-left square of the picture shown
                    before_clicks = len(json.loads(vr.ssh(host, 'curl -s localhost:8099/clicks') or '[]'))
                    for t in ('mouseMoved', 'mousePressed', 'mouseReleased'):
                        await c.call('Input.dispatchMouseEvent', type=t, x=x, y=y, button='left', buttons=1 if t == 'mousePressed' else 0, clickCount=1)
                        await asyncio.sleep(.3)
                    await asyncio.sleep(1.5)
                    clicks = json.loads(vr.ssh(host, 'curl -s localhost:8099/clicks') or '[]')[before_clicks:]
                    shot = await c.call('Page.captureScreenshot')
                    open(os.path.join(args.out, f'{host}-{name}-control.png'), 'wb').write(base64.b64decode(shot['data']))
                    print(json.dumps({'host': host, 'config': name, 'canvas': box, 'clicked': [round(x, 1), round(y, 1)], 'page_received': clicks}), flush=True)
                    await c.eval("document.querySelector('.vnc-control').click()")      # back to view only
                    await asyncio.sleep(1)
        finally:
            vr.ssh(host, f'{ENV} wlr-randr --output {output} --transform normal')
            await api.send('Settings/Display/set_orientation', viewer_id=viewer, window_id=win, orientation=before['orientation'])
            await api.send('Settings/Display/set_flip', viewer_id=viewer, window_id=win, flip=before['flip'])
            if before['playlist']:
                await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=win, playlist_id=before['playlist'])
            else:
                await api.send('Viewers/unassign_playlist', viewer_id=viewer, window_id=win)
            await asyncio.sleep(2)
            await api.send('Scheduler/delete_playlist', playlist_id=playlist)
            await asyncio.sleep(1)
            vr.ssh(host, f'pkill -P {net}; kill {net} $(cat {REMOTE}/pid); true')
            chrome.terminate()


asyncio.run(main())
