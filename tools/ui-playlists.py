#!/usr/bin/env python3
"""
Interaction test of the Scheduler page with two playlists, in headless Chrome: creates a playlist through the UI,
adds an asset to it, checks the other playlist does not show it, copies it back, renames the playlist, then removes
everything it created. Prints one line per check and exits 1 if any fails.

    tools/ui-playlists.py --host 192.168.122.50
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

CLICK = """(text) => { const els = [...document.querySelectorAll('button, .v-tab, .v-list-item, .v-btn')];
  const el = els.find(e => e.innerText && e.innerText.trim().toLowerCase() === text.toLowerCase());
  if (!el) return false; el.click(); return true }"""


async def main(args):
    profile = tempfile.mkdtemp()
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9334', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={profile}', '--window-size=1500,1000',
                               'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9334/json/list'))
                            if t['type'] == 'page')
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

            async def text():
                return await cdp.eval('document.body.innerText') or ''

            async def click(label):
                return await cdp.eval(f'({CLICK})({json.dumps(label)})')

            async def wait_for(fragment, seconds=8):
                for _ in range(int(seconds * 4)):
                    if fragment in await text():
                        return True
                    await asyncio.sleep(0.25)
                return False

            async def set_value(selector, value):
                await cdp.eval(f"""(() => {{ const i = document.querySelector({json.dumps(selector)});
                  i.value = {json.dumps(value)}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")

            # create a playlist through the UI
            await cdp.eval("document.querySelector('button[title=\"New playlist\"]').click()")
            await asyncio.sleep(0.6)
            await set_value('.v-dialog input[type=text]', 'UI test')
            await asyncio.sleep(0.3)
            await click('Create')
            check('the new playlist appears as a tab', await wait_for('UI TEST'))
            await click('UI test')
            await asyncio.sleep(1)
            # add a URL asset to it
            await click('Add URL')
            await asyncio.sleep(0.6)
            inputs = await cdp.eval("[...document.querySelectorAll('.v-dialog input')].map(i => i.type + ':' + i.id).join(',')")
            await cdp.eval("""(() => { const ins = [...document.querySelectorAll('.v-dialog input[type=text]')];
              const set = (i, v) => { i.value = v; i.dispatchEvent(new Event('input', {bubbles: true})); };
              set(ins[0], 'only-in-ui-test'); set(ins[1], 'https://example.com/ui-test'); })()""")
            await asyncio.sleep(0.4)
            await click('Save')
            check('the asset shows in the new playlist', await wait_for('only-in-ui-test'))
            await click('Default')
            await asyncio.sleep(1.2)
            check('the other playlist does not show it', 'only-in-ui-test' not in await text())
            await click('UI test')
            await asyncio.sleep(1.2)
            check('switching back shows it again', 'only-in-ui-test' in await text())
            # rename through the playlist settings
            await cdp.eval("document.querySelector('button[title=\"Settings\"]').click()")
            await asyncio.sleep(0.6)
            await set_value('.v-dialog input[type=text]', 'UI test renamed')
            await asyncio.sleep(0.3)
            await click('Save')
            check('the playlist is renamed', await wait_for('UI TEST RENAMED'))
    finally:
        chrome.terminate()
        # clean up through the API, whatever happened above
        import ssl
        sslctx = ssl.create_default_context()
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE
        proc = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('ws-probe.py')), '--host', args.host,
                               '--send', 'Scheduler/playlists', '--timeout', '4'], capture_output=True, text=True)
        import re
        m = re.search(r'"playlists": (\[.*?\n  \])', proc.stdout, re.S)
        for p in json.loads(m.group(1)) if m else []:
            if p['name'].startswith('UI test'):
                subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('ws-probe.py')), '--host', args.host,
                                '--send', 'Scheduler/delete_playlist', '--args',
                                json.dumps({'playlist_id': p['playlist_id']}), '--timeout', '4'], capture_output=True)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    asyncio.run(main(ap.parse_args()))
