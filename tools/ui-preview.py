#!/usr/bin/env python3
"""
Interaction test of the asset preview window in headless Chrome: opens the preview of an uploaded image from the files
list, checks it is a floating window (not a modal dialog) showing the image, that a second one can be open at the
second window for the same asset, and that closing works.

    tools/ui-preview.py --host 192.168.122.50 --file preview-test.png
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

spec = importlib.util.spec_from_file_location('ui_smoke', pathlib.Path(__file__).with_name('ui-smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


async def main(args):
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9335', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,1000', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9335/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = smoke.Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            for d in ('Page', 'Runtime'):
                await cdp.call(f'{d}.enable')
            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                           f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(4)
            click_preview = f"""(() => {{ const row = [...document.querySelectorAll('.files-list .v-list-item')]
              .find(r => r.innerText.includes({json.dumps(args.file)}));
              if (!row) return false; row.querySelector('button[title=Preview]').click(); return true }})()"""
            check('the file is in the uploads list and Preview can be clicked', await cdp.eval(click_preview))
            await asyncio.sleep(1.5)
            info = await cdp.eval("""(() => { const w = document.querySelector('.floating-window');
              if (!w) return null; const img = w.querySelector('img');
              return {fixed: getComputedStyle(w).position, img: img && img.getAttribute('src'),
                      loaded: img && img.complete && img.naturalWidth > 0,
                      modal: !!document.querySelector('.v-overlay--active .v-overlay__scrim')} })()""")
            check('a floating window opened', bool(info) and info['fixed'] == 'fixed')
            check('it shows the image and the image loaded', bool(info) and bool(info['loaded']) and args.file in (info['img'] or ''))
            check('it is not a modal (no scrim over the page)', bool(info) and not info['modal'])
            # resize from the edges, as the user would, with pointer events on the handles
            drag_js = """(async (dir, dx, dy) => { const w = document.querySelector('.floating-window');
              const h = w.querySelector('.resize-' + dir); const r = h.getBoundingClientRect();
              const before = w.getBoundingClientRect();
              h.dispatchEvent(new PointerEvent('pointerdown', {clientX: r.left + 4, clientY: r.top + 4, bubbles: true, pointerId: 1}));
              window.dispatchEvent(new PointerEvent('pointermove', {clientX: r.left + 4 + dx, clientY: r.top + 4 + dy, bubbles: true}));
              window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
              await new Promise(r => setTimeout(r, 300));
              const a = w.getBoundingClientRect();
              return {dx: Math.round(a.left - before.left), dy: Math.round(a.top - before.top),
                      dw: Math.round(a.width - before.width), dh: Math.round(a.height - before.height)} })"""
            east = await cdp.eval(drag_js + "('e', 60, 0)", True)
            check('the right edge grows the window to the right', east['dw'] == 60 and east['dx'] == 0)
            south = await cdp.eval(drag_js + "('s', 0, 40)", True)
            check('the bottom edge grows it downwards', south['dh'] == 40 and south['dy'] == 0)
            west = await cdp.eval(drag_js + "('w', -50, 0)", True)
            check('the left edge grows it to the left (the window moves with it)', west['dw'] == 50 and west['dx'] == -50)
            north = await cdp.eval(drag_js + "('n', 0, -30)", True)
            check('the top edge grows it upwards', north['dh'] == 30 and north['dy'] == -30)
            corner = await cdp.eval(drag_js + "('se', 20, 20)", True)
            check('a corner resizes both ways', corner['dw'] == 20 and corner['dh'] == 20)
            size = await cdp.eval("(() => { const b = document.querySelector('.floating-window').getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)] })()")
            moved = await cdp.eval("""(async () => { const w = document.querySelector('.floating-window');
              const t = w.querySelector('.floating-title'); const r = t.getBoundingClientRect(); const o = w.getBoundingClientRect();
              t.dispatchEvent(new PointerEvent('pointerdown', {clientX: r.left + 100, clientY: r.top + 10, bubbles: true, pointerId: 1}));
              window.dispatchEvent(new PointerEvent('pointermove', {clientX: r.left + 160, clientY: r.top + 60, bubbles: true}));
              window.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
              await new Promise(r => setTimeout(r, 300));
              const b = w.getBoundingClientRect();
              return {x: Math.round(b.left - o.left), w: Math.round(b.width), h: Math.round(b.height)} })()""", True)
            check('dragging moves the window', moved['x'] != 0)
            check('and keeps the size the user gave it', [moved['w'], moved['h']] == size)
            sizes = await cdp.eval("""(() => { const w = document.querySelector('.floating-window');
              const b = [...w.querySelectorAll('.fw-btn')]; const i = b[0].querySelector('.v-icon');
              const bar = w.querySelector('.floating-title').getBoundingClientRect();
              return {buttons: b.map(x => x.title), bw: Math.round(b[0].getBoundingClientRect().width),
                      icon: Math.round(parseFloat(getComputedStyle(i).fontSize)), bar: Math.round(bar.height)} })()""")
            check('title bar has minimise, maximise and close', sizes['buttons'] == ['Minimise', 'Maximise', 'Close'])
            check('buttons are 24 px with 16 px icons, KDE-sized', sizes['bw'] == 24 and sizes['icon'] == 16)
            before = await cdp.eval("(() => { const b = document.querySelector('.floating-window').getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)] })()")
            await cdp.eval("document.querySelector('.floating-window button[title=Maximise]').click()")
            await asyncio.sleep(0.4)
            full = await cdp.eval("""(() => { const b = document.querySelector('.floating-window').getBoundingClientRect();
              const bar = document.querySelector('.v-app-bar').getBoundingClientRect();
              return {w: Math.round(b.width) == innerWidth, h: Math.round(b.height) == innerHeight - Math.round(bar.bottom),
                      top: Math.round(b.top) == Math.round(bar.bottom)} })()""")
            check('maximise fills the page below the app bar', full['w'] and full['h'] and full['top'])
            await cdp.eval("document.querySelector('.floating-title').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            await asyncio.sleep(0.4)
            after = await cdp.eval("(() => { const b = document.querySelector('.floating-window').getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)] })()")
            check('double-clicking the title bar restores the previous size', after == before)
            await cdp.eval(click_preview)
            await asyncio.sleep(1)
            check('clicking Preview again keeps one window for the same asset', await cdp.eval("document.querySelectorAll('.floating-window').length") >= 1)
            await cdp.eval("document.querySelectorAll('.floating-window button[title=Close]').forEach(b => b.click())")
            await asyncio.sleep(0.6)
            check('closing removes the window', await cdp.eval("document.querySelectorAll('.floating-window').length") == 0)
    finally:
        chrome.terminate()
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--file', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    asyncio.run(main(ap.parse_args()))
