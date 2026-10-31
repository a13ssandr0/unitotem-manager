#!/usr/bin/env python3
"""
Runs ON the board: kills every renderer process of the viewer (SIGKILL) and waits for the window to show its content again.
What the window shows is read over DevTools before and after (the sources of the iframes/media of its panes). Usage: crash-check.py [window id]
"""
import asyncio
import json
import os
import signal
import sys
import time
import urllib.request

import websockets

SHOWN = """(window.__crashMarker ? 'OLD DOCUMENT ' : '') + JSON.stringify([...document.querySelectorAll('.pane iframe, .pane img, .pane video')].map(e => e.getAttribute('src') || e.currentSrc).filter(Boolean))"""


def targets():
    return json.load(urllib.request.urlopen('http://127.0.0.1:9223/json'))


async def shown(window):
    page = next((t for t in targets() if t['type'] == 'page' and f'window={window}' in t['url']), None)
    if page is None:
        return None
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
        await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': SHOWN}}))
        while True:
            reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if reply.get('id') == 1:
                return reply['result']['result'].get('value')


def renderers():
    out = []
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            args = open(f'/proc/{pid}/cmdline').read()       # Chromium rewrites its title into one string
        except OSError:
            continue
        if '--type=renderer' in args:
            out.append(int(pid))
    return out


async def mark(window):
    page = next(t for t in targets() if t['type'] == 'page' and f'window={window}' in t['url'])
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
        await ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': 'window.__crashMarker = 1'}}))
        await ws.recv()


async def main(window):
    before = await shown(window)
    await mark(window)               # a document that survives the kill would still carry it
    print('before:', before)
    pids = renderers()
    print('killing renderers', pids)
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
    start = time.time()
    after = None
    while time.time() - start < 40:
        await asyncio.sleep(1)
        try:
            after = await shown(window)
        except Exception:
            continue
        if after and after == before and not set(pids) & set(renderers()):
            break
    print(f'after {time.time() - start:.0f} s:', after, '| new renderers', renderers())
    sys.exit(0 if after and after == before else 1)


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
