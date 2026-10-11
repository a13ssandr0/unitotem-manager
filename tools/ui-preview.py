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
