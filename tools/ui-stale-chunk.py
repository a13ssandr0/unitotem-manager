#!/usr/bin/env python3
"""
A tab left open across an update must recover by itself: make the first request for a page chunk fail with 404 (what an
old tab gets after the files were replaced) and click the menu entry. The page must load again and show the page.

    tools/ui-stale-chunk.py --host 192.168.122.50
"""
import argparse
import asyncio
import base64
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
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9341', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9341/json/list')) if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            cdp = smoke.Cdp(ws)
            failed = []

            async def pump():
                async for raw in ws:
                    msg = json.loads(raw)
                    if 'id' in msg:
                        fut = cdp.pending.pop(msg['id'], None)
                        if fut is not None:
                            fut.set_result(msg)
                    elif msg.get('method') == 'Fetch.requestPaused':
                        p = msg['params']
                        url = p['request']['url']
                        if '/assets/audio-' in url and not failed:
                            failed.append(url)
                            await ws.send(json.dumps({'id': 9000 + len(failed), 'method': 'Fetch.fulfillRequest',
                                                      'params': {'requestId': p['requestId'], 'responseCode': 404,
                                                                 'body': base64.b64encode(b'gone').decode()}}))
                        else:
                            await ws.send(json.dumps({'id': 9100 + int(time.time() * 1000) % 100000, 'method': 'Fetch.continueRequest',
                                                      'params': {'requestId': p['requestId']}}))
                    else:
                        cdp.events.append(msg)

            asyncio.get_running_loop().create_task(pump())
            for d in ('Page', 'Runtime'):
                await cdp.call(f'{d}.enable')
            base = f'https://{args.host}'
            await cdp.call('Page.navigate', url=base + '/login')
            await asyncio.sleep(2)
            await cdp.eval("fetch('/auth/token',{method:'POST',credentials:'include',body:new URLSearchParams("
                           f"{{username:'{args.user}',password:'{args.password}'}})}}).then(r=>r.status)", True)
            await cdp.call('Page.navigate', url=base + '/')
            await asyncio.sleep(4)
            await cdp.call('Fetch.enable', patterns=[{'urlPattern': '*/assets/*', 'requestStage': 'Request'}])
            await cdp.eval("document.querySelector('a[href=\"/settings/audio\"]').click()")
            for _ in range(40):
                await asyncio.sleep(0.5)
                state = await cdp.eval("""({path: location.pathname,
                  title: document.querySelector('.v-card-title')?.innerText ?? null,
                  reloaded: !!sessionStorage.getItem('unitotem-chunk-reload')})""")
                if state and state['path'] == '/settings/audio' and state['title'] == 'Audio':
                    ok = True
                    break
            print('final state:', state, '| reload marker set:', state and state['reloaded'])
            print(('PASS' if ok else 'FAIL') + f' the first chunk request failed ({len(failed)} forced 404) and the page still loaded')
    finally:
        chrome.terminate()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    asyncio.run(main(ap.parse_args()))
