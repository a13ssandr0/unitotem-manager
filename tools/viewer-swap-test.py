#!/usr/bin/env python3
"""Headless Chrome test of the page swap in the viewer's boot-screen.html.

Serves the page and test assets from a local HTTP server, starts google-chrome headless with a
remote-debugging port, drives show() over CDP and samples every container and the web iframe on
each animation frame. Assertions are on ordering; timing checks have wide margins.

Scenarios:
  A  image -> web (slow page, 3 s): the web frame is never visible before its load event, the
     image stays visible until then, the previous page is never visible after the image.
  B  web -> web (slow -> fast): recorded only, the final state must be the new page.
  C  image -> web -> image while the web page loads: afterwards the image is shown, the iframe is
     display:none and its visibility is not left hidden, and no later frame shows the iframe.
  D  (--slow) a page the server holds for 15 s is revealed after about 10 s, the image stays visible.

Run from unitotem-manager/:
  .venv/bin/python tools/viewer-swap-test.py [PAGE] [--slow] [--tmp-root DIR] [--chrome PATH]

PAGE defaults to manager/plugins/unitotem_viewer/static/boot-screen.html. It is read at start and
served as /boot-screen.html from 127.0.0.1 (same origin as the test assets).
--tmp-root: the Chrome profile is created under it and removed at the end (default: system temp dir).
Exit status: 0 all assertions pass, 1 an assertion failed, 2 the test could not run.
"""
import argparse
import asyncio
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.client import connect

DEFAULT_PAGE = Path(__file__).resolve().parent.parent / 'manager/plugins/unitotem_viewer/static/boot-screen.html'
SLOW_DELAY = 3.0       # seconds the server waits before /slow.html
HOLD_DELAY = 15.0      # seconds the server holds /hold.html (must exceed the reveal timeout)
TOLERANCE_MS = 20      # sampling granularity allowed when comparing a frame with an event

# Samples and marks are taken on the page's performance.now() clock.
SETUP_JS = r"""
(() => {
  window.__errors = [];
  window.__marks = [];
  window.__samples = [];
  window.addEventListener('error', e => window.__errors.push(String(e.message)));
  window.addEventListener('unhandledrejection', e => window.__errors.push('rejection: ' + String(e.reason)));
  const pane = document.querySelector('#panes .pane');
  const web = pane.querySelector('.web'), img = pane.querySelector('.image');
  const boot = pane.querySelector('.boot'), video = pane.querySelector('.video'), audio = pane.querySelector('.audio');
  const shown = el => { const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden' ? 1 : 0; };
  const pathOf = s => { try { return new URL(s, location.href).pathname; } catch (e) { return 'ERR'; } };
  const frameHref = () => { try { return web.contentWindow.location.href; } catch (e) { return 'ERR:' + e.name; } };
  window.__mark = (kind, p) => { const t = performance.now(); window.__marks.push({t, kind, path: p}); return t; };
  document.addEventListener('load', e => {
    if (e.target === web) window.__mark('load', pathOf(frameHref()));
  }, true);
  const tick = () => {
    const cs = getComputedStyle(web);
    window.__samples.push({t: performance.now(), b: shown(boot), w: shown(web), i: shown(img),
                           v: shown(video), a: shown(audio), wd: cs.display, wv: cs.visibility,
                           h: pathOf(frameHref())});
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return true;
})()
"""

PNG_PATH_NOTE = 'image.png is generated in memory (320x180, red)'


def make_png(w, h, rgb):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.server.routes.get(self.path.split('?')[0])
        if route is None:
            self.send_error(404)
            return
        ctype, body, delay = route
        if delay:
            time.sleep(delay)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_http(page_bytes):
    html = 'text/html; charset=utf-8'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.handle_error = lambda request, client_address: None      # the held page may be closed by Chrome
    server.routes = {
        '/boot-screen.html': (html, page_bytes, 0),
        '/image.png': ('image/png', make_png(320, 180, (200, 30, 30)), 0),
        '/fast.html': (html, b'<!doctype html><meta charset="utf-8"><title>FAST</title><body>FAST</body>', 0),
        '/slow.html': (html, b'<!doctype html><meta charset="utf-8"><title>SLOW</title><body>SLOW</body>', SLOW_DELAY),
        '/hold.html': (html, b'<!doctype html><meta charset="utf-8"><title>HOLD</title><body>HOLD</body>', HOLD_DELAY),
    }
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def start_chrome(chrome, port, profile):
    cmd = [chrome, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={profile}',
           '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--window-size=1280,720',
           '--remote-allow-origins=*', '--disable-renderer-backgrounding',
           '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows', 'about:blank']
    # own process group: the saved PID names the group that is killed at the end
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def stop_chrome(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(10)
    except ProcessLookupError:
        pass


def page_ws_url(port, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with opener.open(f'http://127.0.0.1:{port}/json/list', timeout=2) as r:
                for target in json.load(r):
                    if target.get('type') == 'page' and target.get('webSocketDebuggerUrl'):
                        return target['webSocketDebuggerUrl']
        except OSError:
            pass
        time.sleep(0.2)
    return None


class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    async def call(self, method, params=None, timeout=20):
        self.next_id += 1
        my_id = self.next_id
        await self.ws.send(json.dumps({'id': my_id, 'method': method, 'params': params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if msg.get('id') == my_id:
                if 'error' in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get('result', {})

    async def ev(self, expr):
        r = await self.call('Runtime.evaluate', {'expression': expr, 'returnByValue': True, 'awaitPromise': False})
        if 'exceptionDetails' in r:
            detail = r['exceptionDetails']
            raise RuntimeError(f"script failed ({expr[:60]!r}): {detail.get('exception', {}).get('description', detail.get('text'))}")
        return r['result'].get('value')


async def wait_for(cdp, expr, timeout, step=0.05):
    end = time.monotonic() + timeout
    while True:
        value = await cdp.ev(expr)
        if value:
            return value
        if time.monotonic() >= end:
            return None
        await asyncio.sleep(step)


async def wait_mark(cdp, kind, path, after, timeout):
    end = time.monotonic() + timeout
    while True:
        for m in await cdp.ev('window.__marks'):
            if m['kind'] == kind and m['path'] == path and m['t'] > after:
                return m['t']
        if time.monotonic() >= end:
            return None
        await asyncio.sleep(0.05)


async def show(cdp, src, container):
    expr = (f"(() => {{ const t = window.__mark('show', {json.dumps(src)}); "
            f"show({json.dumps(src)}, {json.dumps(container)}, 'contain', 'rgb(0,0,0)', 1, false, 1, false, false); "
            f"return t; }})()")
    return await cdp.ev(expr)


def transitions(samples, t_from, t_to, limit=40):
    rows, last = [], None
    for s in samples:
        if s['t'] < t_from or s['t'] > t_to:
            continue
        state = (s['b'], s['w'], s['i'], s['v'], s['a'], s['wd'], s['wv'], s['h'])
        if state != last:
            rows.append((s['t'], state))
            last = state
    lines = []
    for t, (b, w, i, v, a, wd, wv, h) in rows[:limit]:
        lines.append(f"  +{(t - t_from) / 1000:7.3f} s  boot={b} web={w} image={i} video={v} audio={a}"
                     f"  iframe.display={wd} iframe.visibility={wv} iframe.page={h}")
    if len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more transitions")
    return lines


async def run(base, debug_port, slow, results, timeline):
    ws_url = await asyncio.to_thread(page_ws_url, debug_port, 20)
    if not ws_url:
        print('ERROR: Chrome did not open a page target')
        return 2
    async with connect(ws_url, max_size=None, proxy=None, open_timeout=10) as ws:
        cdp = Cdp(ws)
        await cdp.call('Page.navigate', {'url': base + '/boot-screen.html'})
        ready = await wait_for(cdp, "document.readyState === 'complete' && typeof show === 'function' && "
                                    "!!document.querySelector('#panes .web')", 20)
        if not ready:
            print('ERROR: boot-screen.html did not load or has no show()')
            return 2
        await cdp.ev(SETUP_JS)

        def add(scen, name, measured, expected, ok):
            results.append((scen, name, str(measured), expected, bool(ok)))

        samples_all = None

        # Priming: a web page is on screen before scenario A.
        t0 = await show(cdp, '/fast.html', 'web')
        t_fast = await wait_mark(cdp, 'load', '/fast.html', t0, 10)
        await asyncio.sleep(0.5)

        # Scenario A: image -> web (slow)
        t_img = await show(cdp, '/image.png', 'image')
        img_ready = await wait_for(cdp, "(() => { const i = document.querySelector('#panes .image');"
                                        " return i.complete && i.naturalWidth > 0; })()", 10)
        await asyncio.sleep(0.5)
        t_show = await show(cdp, '/slow.html', 'web')
        t_load = await wait_mark(cdp, 'load', '/slow.html', t_show, 15)
        await asyncio.sleep(1.0)
        a_start = t_img
        samples_all = await cdp.ev('window.__samples')
        S = [s for s in samples_all if s['t'] >= t_show]
        if t_load is None:
            add('A', 'web page load event', 'none within 15 s', 'load event', False)
        else:
            before = [s for s in S if s['t'] < t_load]
            web_before = sum(s['w'] for s in before)
            img_gap = sum(1 for s in before if not s['i'])
            add('A', 'web frame visible before its load event', f'{web_before} of {len(before)} frames',
                '0 frames', web_before == 0)
            add('A', 'image visible from show() to load event', f'{img_gap} frames without image',
                '0 frames', img_gap == 0)
            after_img = [s for s in samples_all if s['t'] >= t_img]
            old = sum(1 for s in after_img if s['w'] and s['h'] == '/fast.html')
            add('A', 'previous page /fast.html never visible after image', f'{old} frames', '0 frames', old == 0)
            first_vis = next((s['t'] for s in S if s['w']), None)
            if first_vis is None:
                add('A', 'first visible web frame', 'never', 'after load', False)
            else:
                add('A', 'first visible web frame not before load event',
                    f'{(first_vis - t_load):+.0f} ms vs load', '>= -20 ms', first_vis >= t_load - TOLERANCE_MS)
                add('A', 'first visible web frame after show()', f'{(first_vis - t_show) / 1000:.2f} s',
                    '2.5 s .. 10 s', 2500 <= first_vis - t_show <= 10000)
            timeline['A'] = (t_img, samples_all)
        gaps = sum(1 for s in samples_all if not (s['b'] or s['w'] or s['i'] or s['v'] or s['a']))
        add('A', 'frames with no container visible', f'{gaps} of {len(samples_all)} frames', '0 frames',
            gaps == 0 and len(samples_all) >= 50)
        add('A', 'image became ready before the swap', 'ready' if img_ready else 'not ready', 'ready', bool(img_ready))

        # Scenario B: web -> web (slow -> fast), recorded only
        t_b = await show(cdp, '/fast.html', 'web')
        t_bl = await wait_mark(cdp, 'load', '/fast.html', t_b, 10)
        await asyncio.sleep(0.5)
        samples_all = await cdp.ev('window.__samples')
        if t_bl is not None:
            hidden = sum(1 for s in samples_all if t_b <= s['t'] < t_bl and not s['w'])
            total = sum(1 for s in samples_all if t_b <= s['t'] < t_bl)
            add('B', 'web frames hidden between show() and load (recorded)', f'{hidden} of {total}',
                'not asserted', True)
        last = samples_all[-1]
        add('B', 'final state shows /fast.html in the web frame', f"web={last['w']} page={last['h']}",
            'web=1 page=/fast.html', last['w'] == 1 and last['h'] == '/fast.html')
        timeline['B'] = (t_b, samples_all)

        # Scenario C: image -> web -> image while the web page loads
        await cdp.ev(f"(() => {{ const t = window.__mark('show', '/image.png');"
                     f" show('/image.png', 'image', 'contain', 'rgb(0,0,0)', 1, false, 1, false, false);"
                     f" window.__mark('show', '/slow.html');"
                     f" show('/slow.html', 'web', 'contain', 'rgb(0,0,0)', 1, false, 1, false, false);"
                     f" const t2 = window.__mark('show', '/image.png');"
                     f" show('/image.png', 'image', 'contain', 'rgb(0,0,0)', 1, false, 1, false, false);"
                     f" return t2; }})()")
        t_c = (await cdp.ev('window.__marks.filter(m => m.kind === "show").pop().t'))
        await asyncio.sleep(4.5)
        samples_all = await cdp.ev('window.__samples')
        S = [s for s in samples_all if s['t'] >= t_c]
        web_late = sum(s['w'] for s in S)
        add('C', 'web frame visible after the image replaced it', f'{web_late} frames', '0 frames', web_late == 0)
        final = samples_all[-1]
        add('C', 'final state: image shown, iframe display none', f"image={final['i']} iframe.display={final['wd']}",
            'image=1 iframe.display=none', final['i'] == 1 and final['wd'] == 'none')
        vis_style = await cdp.ev("document.querySelector('#panes .web').style.visibility")
        add('C', 'iframe visibility not left hidden', f"style.visibility='{vis_style}'", "not 'hidden'",
            vis_style != 'hidden')
        timeline['C'] = (t_c - 0, samples_all)

        if slow:
            # Scenario D: a page that never loads in time is revealed after about 10 s
            t_d = await show(cdp, '/hold.html', 'web')
            await asyncio.sleep(14.5)
            samples_all = await cdp.ev('window.__samples')
            S = [s for s in samples_all if s['t'] >= t_d]
            first_vis = next((s['t'] for s in S if s['w']), None)
            if first_vis is None:
                add('D', 'held page revealed', 'never within 14.5 s', '8 s .. 12.5 s', False)
            else:
                delay = first_vis - t_d
                add('D', 'held page revealed after timeout', f'{delay / 1000:.2f} s', '8 s .. 12.5 s',
                    8000 <= delay <= 12500)
                before = [s for s in S if s['t'] < first_vis]
                gap = sum(1 for s in before if not s['i'])
                add('D', 'image visible until the reveal', f'{gap} frames without image', '0 frames', gap == 0)
            timeline['D'] = (t_d, samples_all)

        errors = await cdp.ev('window.__errors')
        return {'errors': errors, 'timeline': timeline}


def print_report(results, timeline, errors):
    head = ('Scenario', 'Check', 'Measured', 'Expected', 'Result')
    rows = [(s, n, m, e, 'PASS' if ok else 'FAIL') for s, n, m, e, ok in results]
    widths = [max(len(str(r[i])) for r in [head] + rows) for i in range(5)]
    fmt = '  '.join('{:<%d}' % w for w in widths)
    print(fmt.format(*head))
    print(fmt.format(*['-' * w for w in widths]))
    for r in rows:
        print(fmt.format(*r))
    for key in sorted(timeline):
        t_ref, samples = timeline[key]
        print(f'\nTimeline {key} (relative to its start, changes only):')
        for line in transitions(samples, t_ref, float('inf')):
            print(line)
    print(f'\nPage errors seen: {len(errors)}')
    for e in errors[:10]:
        print('  ' + e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('page', nargs='?', default=str(DEFAULT_PAGE), help='boot-screen.html to test')
    ap.add_argument('--slow', action='store_true', help='add scenario D (held page revealed after about 10 s)')
    ap.add_argument('--tmp-root', default=None, help='directory for the Chrome profile (default: system temp dir)')
    ap.add_argument('--chrome', default=None, help='Chrome binary (default: google-chrome from PATH)')
    args = ap.parse_args()

    page = Path(args.page).resolve()
    chrome = args.chrome or shutil.which('google-chrome')
    if not page.is_file() or not chrome:
        print(f'ERROR: page {page} or Chrome not found')
        return 2
    print(f'page:   {page}')
    print(f'chrome: {chrome}')
    print(f'assets: {PNG_PATH_NOTE}; slow page delay {SLOW_DELAY} s, held page {HOLD_DELAY} s\n')

    server = start_http(page.read_bytes())
    base = f'http://127.0.0.1:{server.server_address[1]}'
    profile = tempfile.mkdtemp(prefix='viewer-swap-profile-', dir=args.tmp_root)
    debug_port = free_port()
    proc = start_chrome(chrome, debug_port, profile)
    results, timeline = [], {}
    try:
        out = asyncio.run(run(base, debug_port, args.slow, results, timeline))
    except (OSError, RuntimeError, asyncio.TimeoutError, TimeoutError) as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}')
        return 2
    finally:
        stop_chrome(proc)
        server.shutdown()
        server.server_close()
        shutil.rmtree(profile, ignore_errors=True)
    if not isinstance(out, dict):
        return out
    print_report(results, timeline, out['errors'])
    failed = [r for r in results if not r[4]]
    print(f'\n{len(results) - len(failed)} of {len(results)} checks passed')
    print('RESULT: PASS' if not failed else f'RESULT: FAIL ({len(failed)} failed)')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
