#!/usr/bin/env python3
"""
Runs ON the board: records what a REAL touch panel sends (raw ABS_X/ABS_Y or the multitouch positions, normalised to its own range) next to
where the viewer's page received the tap (pointerdown, as a fraction of the window), so the relation between glass and picture is read from
real taps. The page side is a DevTools listener installed in the first window; run for SECONDS, then print one line per tap.

    touchrec.py DEVICE_NAME SECONDS
"""
import asyncio
import fcntl
import json
import os
import struct
import sys
import time
import urllib.request

import websockets

name, seconds = sys.argv[1], int(sys.argv[2])
EV_ABS, EV_KEY, EV_SYN = 3, 1, 0
ABS_X, ABS_Y, ABS_MT_X, ABS_MT_Y, ABS_MT_SLOT, ABS_MT_ID = 0, 1, 0x35, 0x36, 0x2f, 0x39
BTN_TOUCH = 0x14a


def nodes():
    out = []
    for n in sorted(os.listdir('/sys/class/input')):
        if n.startswith('event'):
            try:
                if open(f'/sys/class/input/{n}/device/name').read().strip() == name:
                    out.append(f'/dev/input/{n}')
            except OSError:
                pass
    return out


def absinfo(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, 0x80184540 + code, buf)          # EVIOCGABS
    value, mn, mx = struct.unpack_from('iii', buf)
    return mn, mx


taps = []      # (time, raw normalised x, y, node)


def reader(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        ranges = {c: absinfo(fd, c) for c in (ABS_X, ABS_Y, ABS_MT_X, ABS_MT_Y) if True}
    except OSError:
        ranges = {}
    state = {'x': None, 'y': None, 'down': False}
    loop = asyncio.get_running_loop()

    def readable():
        try:
            data = os.read(fd, 24 * 64)
        except BlockingIOError:
            return
        for off in range(0, len(data) - 23, 24):
            _s, _u, t, c, v = struct.unpack_from('qqHHi', data, off)
            if t == EV_ABS and c in (ABS_X, ABS_MT_X) and c in ranges:
                state['x'] = (v - ranges[c][0]) / max(1, ranges[c][1] - ranges[c][0])
            elif t == EV_ABS and c in (ABS_Y, ABS_MT_Y) and c in ranges:
                state['y'] = (v - ranges[c][0]) / max(1, ranges[c][1] - ranges[c][0])
            elif t == EV_KEY and c == BTN_TOUCH and v == 1:
                state['down'] = True
            elif t == EV_SYN and state['down'] and state['x'] is not None:
                taps.append((time.time(), state['x'], state['y'], path, ranges))
                state['down'] = False
    loop.add_reader(fd, readable)
    print('listening on', path, {hex(k): v for k, v in ranges.items()}, flush=True)


async def page():
    targets = json.load(urllib.request.urlopen('http://127.0.0.1:9223/json/list'))
    url = next(t['webSocketDebuggerUrl'] for t in targets if t.get('type') == 'page' and 'boot-screen.html' in t.get('url', ''))
    got = []
    async with websockets.connect(url, max_size=None) as ws:
        n = 0

        async def call(method, **p):
            nonlocal n
            n += 1
            await ws.send(json.dumps({'id': n, 'method': method, 'params': p}))
        await call('Runtime.enable')
        await call('Runtime.addBinding', name='utTapRec')
        await call('Runtime.evaluate', expression='addEventListener("pointerdown", e => utTapRec(JSON.stringify([e.clientX / innerWidth, e.clientY / innerHeight, e.pointerType, innerWidth, innerHeight])), true)')
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            except asyncio.TimeoutError:
                continue
            if msg.get('method') == 'Runtime.bindingCalled':
                got.append((time.time(), json.loads(msg['params']['payload'])))
    return got


async def main():
    for path in nodes():
        reader(path)
    got = await page()
    for t, x, y, path, _ in taps:
        near = min(got, key=lambda g: abs(g[0] - t), default=None)
        page_xy = near[1] if near and abs(near[0] - t) < 1 else None
        print(json.dumps({'raw': [round(x, 3), round(y, 3)], 'page': page_xy[:2] if page_xy else None, 'type': page_xy[2] if page_xy else None,
                          'window': page_xy[3:] if page_xy else None, 'node': path}))
    print('taps', len(taps), 'page events', len(got))

asyncio.run(main())
