#!/usr/bin/env python3
"""
Drag-and-drop reorder of the Scheduler page with two playlists, in headless Chrome with a real mouse: the page's handle (a drag icon that shows while the pointer
is on a row) is pressed, the row is dragged over another one and dropped. DevTools intercepts the native drag (`Input.setInterceptDrags`) and the test drops it
where the mouse is, like a person would. The order is read back from the node's own API, and the other playlist must not move. Everything it creates is removed.

    tools/ui-reorder.py --host <host> --user root --password unitotem
"""
import argparse
import asyncio
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets

HERE = pathlib.Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load('ui_smoke', 'ui-smoke.py')
board = load('board_run', 'ticker/board-run.py')
PORT = 9348


async def main(args):
    api = await board.Api(args.host).__aenter__()
    failures, made = [], []

    def check(label, ok, detail=''):
        print(('ok      ' if ok else 'FAILED  ') + label + (f'   {detail}' if detail else ''))
        if not ok:
            failures.append(label)

    async def order(playlist_id):
        items = (await api.call('Scheduler/assets', playlist_id=playlist_id)).get('items', [])
        return [i['name'] for i in items]

    chrome = None
    try:
        for name, count in (('reorder-a', 3), ('reorder-b', 2)):
            reply = await api.call('Scheduler/create_playlist', reply='Scheduler/playlists', name=name)
            pid = next(p['playlist_id'] for p in reply['playlists'] if p['name'] == name)
            made.append(pid)
            await api.send('Scheduler/add_url_assets', playlist_id=pid, items=[
                {'url': f'https://example.com/{name}/{n}', 'name': f'{name}-{n}', 'duration': 30, 'media_type': 0} for n in range(1, count + 1)])
            await asyncio.sleep(2.5)
        a, b = made
        before_a, before_b = await order(a), await order(b)
        check('two playlists exist with 3 and 2 assets', len(before_a) == 3 and len(before_b) == 2, f'{before_a} {before_b}')

        chrome = subprocess.Popen(['google-chrome', '--headless=new', f'--remote-debugging-port={PORT}', '--no-sandbox', '--ignore-certificate-errors',
                                   f'--user-data-dir={tempfile.mkdtemp()}', '--window-size=1500,1100', 'about:blank'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = smoke.Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for domain in ('Page', 'Runtime'):
                await cdp.call(f'{domain}.enable')
            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                           f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(5)
            await cdp.eval("[...document.querySelectorAll('.v-tab')].find(t => t.innerText.trim().toLowerCase() === 'reorder-a').click()")
            await asyncio.sleep(1.5)

            async def row_box(index):
                return await cdp.eval(f"""(() => {{ const r = [...document.querySelectorAll('.playlist-list .v-list-item')][{index}];
                  if (!r) return null; const b = r.getBoundingClientRect(); return {{x: b.x + 30, y: b.y + b.height / 2, w: b.width, h: b.height}}; }})()""")

            async def mouse(kind, x, y, **extra):
                # while the button is down every move says so (button and buttons), or the browser never starts a drag
                await cdp.call('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left', buttons=1 if kind != 'mouseReleased' else 0,
                               clickCount=1 if kind != 'mouseMoved' else 0, **extra)

            async def drag(from_index, to_index):
                start, end = await row_box(from_index), await row_box(to_index)
                await cdp.call('Input.dispatchMouseEvent', type='mouseMoved', x=start['x'], y=start['y'])     # the handle shows while the pointer is on the row
                await asyncio.sleep(0.5)
                handle = await cdp.eval(f"""(() => {{ const r = [...document.querySelectorAll('.playlist-list .v-list-item')][{from_index}];
                  const h = r.querySelector('.mdi-drag-horizontal'); if (!h) return null; const b = h.getBoundingClientRect();
                  return {{x: b.x + b.width / 2, y: b.y + b.height / 2}}; }})()""")
                if handle is None:
                    print('   (no handle: rows', await cdp.eval("document.querySelectorAll('.playlist-list .v-list-item').length"), 'handles',
                          await cdp.eval("document.querySelectorAll('.mdi-drag-horizontal').length"), 'start', start, ')')
                    return False
                await cdp.call('Input.setInterceptDrags', enabled=True)
                cdp.events.clear() if hasattr(cdp, 'events') else None
                await mouse('mousePressed', handle['x'], handle['y'])
                for step in range(1, 9):
                    y = handle['y'] + (end['y'] - handle['y']) * step / 8
                    await mouse('mouseMoved', handle['x'], y)
                    await asyncio.sleep(0.05)
                intercepted = next((e for e in getattr(cdp, 'events', []) if e.get('method') == 'Input.dragIntercepted'), None)
                if intercepted is None:
                    await asyncio.sleep(0.5)
                    intercepted = next((e for e in getattr(cdp, 'events', []) if e.get('method') == 'Input.dragIntercepted'), None)
                if intercepted is None:
                    print('   (events seen:', sorted({e.get('method') for e in getattr(cdp, 'events', [])}), ')')
                data = intercepted['params']['data'] if intercepted else {'items': [], 'dragOperationsMask': 1}
                for kind in ('dragEnter', 'dragOver', 'dragOver'):
                    await cdp.call('Input.dispatchDragEvent', type=kind, x=handle['x'], y=end['y'], data=data)
                    await asyncio.sleep(0.15)
                await cdp.call('Input.dispatchDragEvent', type='drop', x=handle['x'], y=end['y'], data=data)
                await mouse('mouseReleased', handle['x'], end['y'])
                await cdp.call('Input.setInterceptDrags', enabled=False)
                return intercepted is not None

            started = await drag(2, 0)
            check('the native drag started from the handle', started)
            await asyncio.sleep(2)
            after_a, after_b = await order(a), await order(b)
            shown = await cdp.eval("[...document.querySelectorAll('.playlist-list .v-list-item')].map(r => r.innerText.split('\\n')[0].trim()).join(',')")
            check('the third asset is now the first one', after_a == [before_a[2], before_a[0], before_a[1]], f'{after_a}')
            check('the page shows the same order', shown.startswith(after_a[0]), shown)
            check('the other playlist did not move', after_b == before_b, f'{after_b}')
    finally:
        if chrome:
            chrome.terminate()
        for pid in made:
            try:
                await api.send('Scheduler/delete_playlist', playlist_id=pid)
            except Exception:
                pass
        await asyncio.sleep(1)
        await api.__aexit__(None, None, None)
    print('FAILED: ' + '; '.join(failures) if failures else 'all ok')
    return 1 if failures else 0


p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--user', default='admin')
p.add_argument('--password', default='admin')
sys.exit(asyncio.run(main(p.parse_args())))
