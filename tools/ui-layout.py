#!/usr/bin/env python3
"""
Layout checks in headless Chrome:
 - the floating action button of the timers and users pages is at the bottom of the screen and its right edge is the
   right edge of the card, at several window widths;
 - the Wake-on-LAN and serial settings of a monitor only show when a mechanism that can use them is selected.

    tools/ui-layout.py --host 192.168.122.50
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
    failures = []

    def check(name, ok):
        print(('PASS ' if ok else 'FAIL ') + name)
        if not ok:
            failures.append(name)

    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9339', '--no-sandbox',
                               '--ignore-certificate-errors', f'--user-data-dir={tempfile.mkdtemp()}',
                               '--window-size=1500,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:9339/json/list')) if t['type'] == 'page')
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

            for path, label in (('/settings/timers', 'timers'), ('/settings/users', 'users')):
                for width in (1500, 1100, 700):
                    await cdp.call('Emulation.setDeviceMetricsOverride', width=width, height=800, deviceScaleFactor=1, mobile=False)
                    await cdp.call('Page.navigate', url=base + path)
                    await asyncio.sleep(3.5)
                    m = await cdp.eval("""(() => { const fab = document.querySelector('.fab-holder .v-btn');
                      const card = document.querySelector('.card-column .v-card'); if (!fab || !card) return null;
                      const f = fab.getBoundingClientRect(), c = card.getBoundingClientRect();
                      return {right: Math.round(c.right - f.right), bottom: Math.round(innerHeight - f.bottom)} })()""")
                    print('   measured', label, width, m)
                    check(f'{label} @ {width}px: the button\'s right edge is the card\'s right edge',
                          m is not None and m['right'] == 0)
                    check(f'{label} @ {width}px: the button is at the bottom of the screen', m is not None and 14 <= m['bottom'] <= 18)

            await cdp.call('Emulation.setDeviceMetricsOverride', width=1500, height=1000, deviceScaleFactor=1, mobile=False)
            await cdp.call('Page.navigate', url=base + '/settings/display')
            await asyncio.sleep(4)
            await cdp.eval("document.querySelector('.v-expansion-panel-title').click()")
            await asyncio.sleep(0.8)

            async def pick(label, option):
                await cdp.eval(f"""(() => {{ const f = [...document.querySelectorAll('.v-select')].find(s => s.innerText.includes({json.dumps(label)}));
                  f.querySelector('.v-field').dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}})); }})()""")
                await asyncio.sleep(0.6)
                await cdp.eval(f"""(() => {{ const i = [...document.querySelectorAll('.v-overlay-container .v-list-item')]
                  .find(x => x.innerText.trim() === {json.dumps(option)}); i.click() }})()""")
                await asyncio.sleep(0.6)

            async def shown():
                t = await cdp.eval("document.querySelector('.v-expansion-panel-text').innerText") or ''
                return 'Wake-on-LAN, for a monitor' in t, 'Serial port:' in t

            check('on Automatic both Wake-on-LAN and serial settings are shown', await shown() == (True, True))
            await pick('Switch on with', 'DDC/CI')
            await pick('Switch off with', 'DDC/CI')
            check('with DDC/CI for on and off neither is shown', await shown() == (False, False))
            await pick('Switch on with', 'Wake-on-LAN')
            check('choosing Wake-on-LAN shows only its settings', await shown() == (True, False))
            await pick('Switch on with', 'Serial')
            check('choosing Serial shows only the serial settings', await shown() == (False, True))
    finally:
        chrome.terminate()
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True)
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password', default='admin')
    asyncio.run(main(ap.parse_args()))
