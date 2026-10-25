#!/usr/bin/env python3
"""
Browser test of webui/src/ticker.js in headless Chrome (CDP), against tools/ticker/testpage.html, which provides the
elements and the CSS of the page contract (docs/layout-and-ticker.md). Needs google-chrome and the `websockets` package.

    .venv/bin/python tools/ui-ticker.py [--shots DIR]

Checks the recycler (bounded DOM with 500 headlines, no jump when the list changes, wrap-around), one and two lanes,
overlay vs reserve, top and bottom, the clock, the hostile-feed rendering (text, not markup) and the template error
fallback. Exit status 1 when anything fails.
"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import websockets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(SimpleHTTPRequestHandler):
    """/src/* is webui/src, everything else is tools/ticker."""

    def translate_path(self, path):
        path = path.split('?')[0]
        if path.startswith('/src/'):
            return os.path.join(ROOT, 'webui', 'src', path[5:])
        return os.path.join(ROOT, 'tools', 'ticker', path.lstrip('/') or 'testpage.html')

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


class Cdp:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.events = ws, 0, {}, []

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg:
                self.pending.pop(msg['id']).set_result(msg)
            else:
                self.events.append(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await fut).get('result', {})

    async def eval(self, expression, await_promise=True):
        r = await self.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=await_promise)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'])
        return r.get('result', {}).get('value')


FAILED = []


def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))
    if not ok:
        FAILED.append(name)


BASE = {'enabled': True, 'position': 'bottom', 'mode': 'reserve', 'rows': 1, 'font_size': 3.0, 'font_unit': 'percent',
        'color': 'rgb(255,255,255)', 'background': 'rgba(0,0,0,0.85)', 'speed': 2.5, 'separator': '   \u2022   ',
        'template': '[b]{time:HH:mm}[/b] - {title}', 'select': {'mode': 'count', 'count': 500, 'seconds': 7200},
        'clock': {'enabled': False, 'position': 'end', 'seconds': False, 'date': False, 'rows': 1}, 'locale': 'en-GB'}


def items(n, now=None, title='Headline'):
    import time
    now = now or time.time()
    return [{'time': now - i * 60, 'source': f'S{i % 3}', 'title': f'{title} number {i} of the long list', 'summary': '', 'link': ''}
            for i in range(n)]


async def apply(c, cfg, its):
    cfg = {**BASE, **cfg, 'module': '/src/ticker.js'}
    await c.eval(f'window.__utTickerConfig = {json.dumps(cfg)}; window.__utTickerItems = {json.dumps(its)}; window.applyTicker()')
    await asyncio.sleep(0.3)


async def inspect(c):
    return await c.eval('JSON.stringify(window.utTicker.inspect())', False) and json.loads(await c.eval('JSON.stringify(window.utTicker.inspect())', False))


async def run(shots):
    profile = tempfile.mkdtemp()
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    chrome = subprocess.Popen(['google-chrome', '--headless=new', '--remote-debugging-port=9344', '--no-sandbox', f'--user-data-dir={profile}',
                               '--window-size=1280,720', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen('http://127.0.0.1:9344/json/list'))
                page = next(t for t in targets if t['type'] == 'page')
                break
            except Exception:
                await asyncio.sleep(0.2)
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None) as ws:
            c = Cdp(ws)
            pump = asyncio.create_task(c.pump())
            await c.call('Page.enable')
            await c.call('Runtime.enable')
            await c.call('Emulation.setDeviceMetricsOverride', width=1280, height=720, deviceScaleFactor=1, mobile=False)
            await c.call('Page.navigate', url=f'http://127.0.0.1:{port}/testpage.html')
            for _ in range(50):
                if await c.eval('window.__ready === true', False):
                    break
                await asyncio.sleep(0.1)

            async def shot(name):
                if shots:
                    r = await c.call('Page.captureScreenshot', format='png')
                    open(os.path.join(shots, name + '.png'), 'wb').write(base64.b64decode(r['data']))

            def errors():
                return [e for e in c.events if e.get('method') == 'Runtime.exceptionThrown']

            # ── recycler with 500 headlines ───────────────────────────────────
            big = items(500)
            await apply(c, {'speed': 25.0}, big)
            await asyncio.sleep(2.0)
            s = await inspect(c)
            check('500 headlines: the bar is on and sized', s['enabled'] and s['barH'] > 30 and s['selected'] == 500, s)
            check('500 headlines: only a few elements exist', 0 < s['domItems'] <= 12, s['domItems'])
            check('the bar reserves its height', await c.eval("getComputedStyle(document.getElementById('area')).getPropertyValue('--ticker-h')") == f"{s['barH']}px")
            first_key = s['lanes'][0]['lastKey']
            await asyncio.sleep(6)
            s2 = await inspect(c)
            check('the crawl advances and recycles (elements are reused, DOM still small)', s2['lanes'][0]['lastKey'] != first_key and s2['domItems'] <= 12 and s2['lanes'][0]['idx'] > s2['domItems'] + 2, s2)
            check('no script errors', not errors(), errors()[:1])
            await shot('01-crawl-500')

            # ── no jump when the list changes ─────────────────────────────────
            pos = 'JSON.stringify([...document.querySelectorAll(".ut-item")].map(e => [e.textContent.slice(0, 24), Math.round(e.getBoundingClientRect().left)]))'
            before = json.loads(await c.eval(pos, False))
            await c.eval('window.utTicker.setItems(%s)' % json.dumps(items(500, title='Fresh')[:1] + big[:300]))
            after = json.loads(await c.eval(pos, False))
            same = [a for a in after if any(b[0] == a[0] and abs(b[1] - a[1]) < 40 for b in before)]
            check('a new list does not move what is on screen', len(same) >= len(before) - 1 and len(before) > 0, (before, after))
            s3 = await inspect(c)
            check('...and the lane goes on after the last placed headline', s3['lanes'][0]['lastKey'] is not None and s3['selected'] == 301, s3)

            # ── wrap around: a tiny list loops, with the separator ────────────
            await apply(c, {'speed': 30.0, 'template': '{title}', 'select': {**BASE['select'], 'count': 3}}, items(3, title='T'))
            await asyncio.sleep(6)
            seen = await c.eval('[...document.querySelectorAll(".ut-item")].map(e => e.textContent).join("|")', False)
            check('a short list wraps around', seen.count('number 0') + seen.count('number 1') + seen.count('number 2') >= 2, seen)

            # ── two lanes ─────────────────────────────────────────────────────
            await apply(c, {'rows': 2, 'speed': 5.0, 'template': '{title}', 'select': BASE['select']}, items(40))
            await asyncio.sleep(3)
            s = await inspect(c)
            lanes_text = json.loads(await c.eval('JSON.stringify([...document.querySelectorAll(".ut-lane")].map(l => l.querySelector(".ut-item") && l.querySelector(".ut-item").textContent.slice(0, 30)))', False))
            check('two rows: two lanes with different headlines', len(s['lanes']) == 2 and lanes_text[0] != lanes_text[1] and all(lanes_text), (s, lanes_text))
            h2 = s['barH']
            await shot('02-two-rows')
            await apply(c, {'rows': 1}, items(40))
            s1 = await inspect(c)
            check('two rows are taller than one', h2 > s1['barH'], (h2, s1['barH']))

            # ── overlay vs reserve, top vs bottom ─────────────────────────────
            panes = "JSON.stringify(document.getElementById('panes').getBoundingClientRect())"
            await apply(c, {'mode': 'overlay', 'position': 'bottom'}, items(20))
            r = json.loads(await c.eval(panes, False))
            check('overlay: the panes keep the whole area', r['height'] == 720 and r['top'] == 0, r)
            await shot('03-overlay-bottom')
            await apply(c, {'mode': 'reserve', 'position': 'bottom'}, items(20))
            r = json.loads(await c.eval(panes, False))
            bh = (await inspect(c))['barH']
            check('reserve at the bottom: the panes shrink by the bar', r['height'] == 720 - bh and r['top'] == 0, (r, bh))
            await shot('04-reserve-bottom')
            await apply(c, {'mode': 'reserve', 'position': 'top'}, items(20))
            r = json.loads(await c.eval(panes, False))
            hb = json.loads(await c.eval("JSON.stringify(document.getElementById('ticker-host').getBoundingClientRect())", False))
            check('reserve at the top: the panes start under the bar', r['top'] == bh and hb['top'] == 0, (r, hb))
            await shot('05-reserve-top')

            # ── clock ─────────────────────────────────────────────────────────
            await apply(c, {'position': 'bottom', 'clock': {'enabled': True, 'position': 'start', 'seconds': True, 'date': True, 'rows': 1}}, items(20))
            txt = await c.eval("document.querySelector('.ut-clock').textContent", False)
            import re
            check('clock with date and seconds on one row', re.fullmatch(r'\w{3} \d{1,2} \w{3}\s+\d\d:\d\d:\d\d', txt) is not None, txt)
            first = await c.eval("document.querySelector('.ut-bar').firstElementChild.className", False)
            check('clock at the start', 'ut-clock' in first, first)
            await asyncio.sleep(2.3)
            txt2 = await c.eval("document.querySelector('.ut-clock').textContent", False)
            check('the seconds clock ticks', txt2 != txt, (txt, txt2))
            await shot('06-clock-start-seconds-date')
            await apply(c, {'clock': {'enabled': True, 'position': 'end', 'seconds': False, 'date': True, 'rows': 2}}, items(20))
            divs = await c.eval("document.querySelectorAll('.ut-clock div').length", False)
            last = await c.eval("document.querySelector('.ut-bar').lastElementChild.className", False)
            txt = await c.eval("document.querySelector('.ut-clock').innerText", False)
            check('clock at the end, two rows, no seconds', divs == 2 and 'ut-clock' in last and re.search(r'\d\d:\d\d\n', txt + '\n') and ':' not in txt.split('\n')[1], (divs, last, txt))
            await shot('07-clock-end-two-rows')
            await apply(c, {'clock': {'enabled': True, 'position': 'end', 'seconds': False, 'date': False, 'rows': 1}}, items(20))
            txt = await c.eval("document.querySelector('.ut-clock').textContent", False)
            check('clock without date or seconds', re.fullmatch(r'\d\d:\d\d', txt) is not None, txt)
            await apply(c, {'clock': {'enabled': False, 'position': 'end', 'seconds': False, 'date': False, 'rows': 1}}, items(20))
            check('no clock when off', await c.eval("document.querySelector('.ut-clock') === null", False))

            # ── hostile feed ──────────────────────────────────────────────────
            bad = [{'time': __import__('time').time(), 'source': '[red]S', 'summary': '',
                    'title': '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>[bold red]RED[/] {title}', 'link': ''}]
            await apply(c, {'template': '[green]{title}[/]', 'speed': 0.5}, bad)
            await asyncio.sleep(1)
            check('feed markup is shown as text', await c.eval("document.querySelector('.ut-item').textContent.includes('<img src=x') && document.querySelector('.ut-item').textContent.includes('[bold red]RED[/] {title}')", False))
            check('feed markup creates no elements and runs nothing', await c.eval("document.querySelectorAll('#ticker-host img, #ticker-host script').length === 0 && !window.__pwned", False))
            check('feed text takes the template colour only', await c.eval("[...document.querySelectorAll('.ut-item span')].filter(s => !s.classList.contains('ut-sep')).every(s => s.style.color === 'green' && !s.style.fontWeight)", False))
            await shot('08-hostile')

            # ── template error: titles, and a console error ───────────────────
            c.events.clear()
            await apply(c, {'template': '[nope]{title}'}, items(5))
            logged = [e for e in c.events if e.get('method') == 'Runtime.consoleAPICalled' and e['params']['type'] == 'error']
            check('an invalid template shows the titles and logs the error', logged and await c.eval("document.querySelector('.ut-item').textContent.startsWith('Headline number 0')", False))

            # ── selection with the device clock ───────────────────────────────
            import time
            now = time.time()
            mixed = [{'time': now - 60, 'source': 's', 'title': 'recent', 'summary': '', 'link': ''},
                     {'time': now - 3 * 3600, 'source': 's', 'title': 'old', 'summary': '', 'link': ''}]
            await apply(c, {'template': '{title}', 'select': {'mode': 'range', 'count': 1, 'seconds': 7200}}, mixed)
            check('range selection', (await inspect(c))['selected'] == 1)
            await apply(c, {'template': '{title}', 'select': {'mode': 'count', 'count': 1, 'seconds': 60}}, mixed)
            check('count selection', (await inspect(c))['selected'] == 1)
            await apply(c, {'template': '{title}', 'select': {'mode': 'today', 'count': 1, 'seconds': 60}}, mixed)
            check('today selection', (await inspect(c))['selected'] in (1, 2))

            # ── nothing to show: no bar, no space ─────────────────────────────
            await apply(c, {'template': '{title}'}, [])
            s = await inspect(c)
            check('no headlines and no clock: the bar takes no room', s['hidden'] and await c.eval("getComputedStyle(document.getElementById('area')).getPropertyValue('--ticker-h')") == '0px', s)
            await apply(c, {'enabled': False}, [])
            check('disabled: nothing left', await c.eval("document.getElementById('ticker-host').children.length === 0 && document.getElementById('area').dataset.ticker === 'off'", False))
            check('no script errors at the end', not errors(), errors()[:1])
            pump.cancel()
    finally:
        chrome.terminate()
        httpd.shutdown()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', help='directory for screenshots')
    args = ap.parse_args()
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
    asyncio.run(run(args.shots))
    print('\nFAILED: ' + ', '.join(FAILED) if FAILED else '\nall passed')
    sys.exit(1 if FAILED else 0)
