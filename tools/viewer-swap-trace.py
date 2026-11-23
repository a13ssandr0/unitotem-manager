#!/usr/bin/env python3
"""
Trace of the page changes of one viewer window on a real node: which containers are on screen, whether the web frame shows
a document other than the one requested, every call of the page's show(), the web frame's load events, and a screenshot at
fixed offsets after each image -> web transition. It only observes: the playlist runs as it does.

The DevTools bridge (port 9223, loopback on the node) is reached through an ssh tunnel opened with the helpers of
deploy/script/lib.sh (_sshpass, SSH_OPTS). The tunnel and the page hooks are removed at exit.

    tools/viewer-swap-trace.py --host 192.0.2.102 --user admin --password-file FILE --window 0 \\
        --seconds 70 --out-dir DIR --prefix viewer-fix-pi3-before

FILE holds the login password only, mode 0600; it goes into the environment of the ssh process, never on this command line.
Outputs: DIR/PREFIX-log.json (every event, times in ms from the start of the trace), DIR/PREFIX-NN-<offset>s.png (screenshots),
and the timeline and per-transition figures on stdout. Times are measured in the page (performance.now); the screenshot
schedule is mapped from the page clock, so it carries the round trip of one DevTools call.
"""
import argparse
import asyncio
import base64
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

DEFAULT_LIB = Path(__file__).resolve().parents[2] / 'deploy' / 'script' / 'lib.sh'

# Installed in the page. Returns the page clock at install (t0). Every hook is removed by window.__vfTrace.stop().
INSTALL_JS = r"""
(() => {
  if (window.__vfTrace && window.__vfTrace.stop) { try { window.__vfTrace.stop(); } catch (e) {} }
  const t0 = performance.now();
  const log = [], shows = [], gaps = [];
  const ticks = {raf: 0, int: 0};
  const push = (ev, o) => { if (log.length < 30000) log.push(Object.assign({t: performance.now(), ev}, o)); };
  const isVisible = el => { if (!el) return false; const cs = getComputedStyle(el); return cs.display !== 'none' && cs.visibility !== 'hidden'; };
  const loaded = {};
  const CONTAINERS = ['boot', 'web', 'image', 'video', 'audio'];
  const state = () => [...document.querySelectorAll('#panes .pane')].map(p => {
    const web = p.querySelector('iframe.web.container');
    return {
      pane: p.dataset.pane,
      shown: CONTAINERS.filter(c => isVisible(p.querySelector('.' + c + '.container'))),
      webVisible: isVisible(web),
      webSrc: web ? web.getAttribute('src') : null,
      webLoaded: loaded[p.dataset.pane] ?? null,
    };
  });
  let lastKey = '';
  const sample = source => {
    ticks[source]++;
    const st = state();
    const key = JSON.stringify(st);
    if (key !== lastKey) { lastKey = key; push('state', {by: source, st}); }
  };
  let rafId = 0, lastFrame = null;
  const frame = now => {
    if (lastFrame !== null && gaps.length < 20000) gaps.push(Math.round((now - lastFrame) * 10) / 10);
    lastFrame = now;
    sample('raf');
    rafId = requestAnimationFrame(frame);
  };
  rafId = requestAnimationFrame(frame);
  const timer = setInterval(() => sample('int'), 50);
  const onLoad = e => {
    const el = e.target;
    if (!(el instanceof HTMLIFrameElement) || !el.classList.contains('web')) return;
    const pane = el.closest('.pane') ? el.closest('.pane').dataset.pane : null;
    loaded[pane] = el.getAttribute('src');
    push('web-load', {pane, src: el.getAttribute('src')});
  };
  document.addEventListener('load', onLoad, true);
  const mo = new MutationObserver(list => {
    for (const m of list) {
      const el = m.target;
      const pane = el.closest && el.closest('.pane') ? el.closest('.pane').dataset.pane : null;
      push('mut', {pane, el: el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).join('.') : ''), attr: m.attributeName});
    }
  });
  mo.observe(document.getElementById('panes'), {subtree: true, attributes: true, attributeFilter: ['style', 'class', 'src']});
  const origShow = window.show;
  const wrapped = function (src, container) {
    const a = Array.from(arguments);
    const rec = {t: performance.now(), src: a[0], container: a[1], idle: !!a[8], pane: a[9] ?? null};
    shows.push(rec);
    push('show', {src: rec.src, container: rec.container, idle: rec.idle, pane: rec.pane});
    const r = origShow.apply(this, arguments);
    if (r && typeof r.then === 'function') {
      r.then(() => push('show-done', {src: rec.src, container: rec.container}),
             e => push('show-error', {src: rec.src, error: String(e)}));
    }
    return r;
  };
  window.show = wrapped;
  const stop = () => {
    cancelAnimationFrame(rafId); clearInterval(timer); mo.disconnect();
    document.removeEventListener('load', onLoad, true);
    if (window.show === wrapped) window.show = origShow;
  };
  window.__vfTrace = {t0, log, shows, gaps, ticks, stop};
  sample('int');
  return t0;
})()
"""


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def open_tunnel(lib, user, host, password, local_port):
    """ssh -N forwarding local_port to the node's 127.0.0.1:9223, started through lib.sh's _sshpass. Own process group."""
    env = dict(os.environ, OTA_AUTH='password', OTA_PASSWORD=password)
    script = ('source "$1" || exit 1; _sshpass ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 '
              '"${SSH_OPTS[@]}" -L "127.0.0.1:$2:127.0.0.1:9223" "$3"')
    return subprocess.Popen(['bash', '-c', script, 'trace', str(lib), str(local_port), f'{user}@{host}'], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)


def close_tunnel(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def wait_port(proc, port, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise SystemExit('ssh tunnel ended: ' + proc.stderr.read().decode(errors='replace')[-400:])
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise SystemExit('tunnel port not open within %s s' % timeout)


class Cdp:
    def __init__(self, ws):
        self.ws, self.n, self.pending = ws, 0, {}

    async def pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg and msg['id'] in self.pending:
                self.pending.pop(msg['id']).set_result(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await asyncio.wait_for(fut, 20)).get('result', {})

    async def eval(self, expression):
        r = await self.call('Runtime.evaluate', expression=expression, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'].get('text', 'evaluate failed'))
        return r.get('result', {}).get('value')


def page_target(port, window):
    targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5))
    pages = [t for t in targets if t.get('type') == 'page']
    hit = [t for t in pages if 'boot-screen' in t.get('url', '') and f'window={window}' in t.get('url', '')]
    return hit[0] if hit else None, [t.get('url', '') for t in pages]


def short(url):
    if not url:
        return '-'
    return url if len(url) <= 48 else '...' + url[-45:]


def analyse(events, t0):
    """Per image -> web transition: time of the stale page, the black time and the time the new page is up (page clock, s)."""
    rel = lambda t: (t - t0) / 1000.0
    states = [(rel(e['t']), e['st'][0]) for e in events if e['ev'] == 'state' and e['st']]
    shows = [e for e in events if e['ev'] == 'show']
    loads = [e for e in events if e['ev'] == 'web-load']
    out = []
    prev = None
    for s in shows:
        cur = s['container']
        if prev == 'image' and cur == 'web':
            ts = rel(s['t'])
            load = next((rel(e['t']) for e in loads if e['src'] == s['src'] and rel(e['t']) >= ts), None)
            end = load if load is not None else ts + 10
            stale = black = 0.0
            new_visible = None
            for i, (t, st) in enumerate(states):
                nxt = states[i + 1][0] if i + 1 < len(states) else t + 1e9
                a, b = max(t, ts), min(nxt, end)
                if b <= a:
                    if t >= ts and new_visible is None and st['webVisible'] and st['webLoaded'] == st['webSrc']:
                        new_visible = t - ts
                    continue
                # a visible frame whose last loaded document is not the requested one (null: loaded before the trace)
                if st['webVisible'] and st['webLoaded'] != st['webSrc']:
                    stale += b - a
                if not st['shown'] and not st['webVisible']:
                    black += b - a
                if new_visible is None and st['webVisible'] and st['webLoaded'] == st['webSrc']:
                    new_visible = a - ts
            out.append({'show_s': round(ts, 3), 'src': short(s['src']), 'load_s': None if load is None else round(load - ts, 3),
                        'stale_old_page_ms': round(stale * 1000), 'nothing_visible_ms': round(black * 1000),
                        'new_page_visible_after_ms': None if new_visible is None else round(new_visible * 1000)})
        prev = cur
    return out


async def run(args):
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    offsets = [float(x) for x in args.offsets.split(',')]
    if args.direct:
        # On the node itself (the image's sshd refuses TCP forwarding): DevTools on its own loopback.
        port, tunnel = 9223, None
    else:
        password = Path(args.password_file).read_text().strip()
        if os.stat(args.password_file).st_mode & 0o077:
            raise SystemExit('password file must be mode 0600')
        port = args.local_port or free_port()
        tunnel = open_tunnel(args.lib, args.user, args.host, password, port)
    cdp = None
    try:
        if tunnel is not None:
            wait_port(tunnel, port, 20)
        target, urls = page_target(port, args.window)
        if target is None:
            raise SystemExit(f'no boot-screen page for window {args.window}; page urls: {urls}')
        async with websockets.connect(target['webSocketDebuggerUrl'], max_size=None, open_timeout=10) as ws:
            cdp = Cdp(ws)
            asyncio.get_running_loop().create_task(cdp.pump())
            await cdp.call('Runtime.enable')
            t0 = await cdp.eval(INSTALL_JS)
            print(f'hooks installed, page clock t0={t0:.1f} ms, window {args.window}')
            seen = 0
            prev_container = None
            pending = []
            transitions = []
            polls = []
            end = time.monotonic() + args.seconds
            while time.monotonic() < end or pending:
                before = time.monotonic()
                data = json.loads(await cdp.eval(
                    f'JSON.stringify({{now: performance.now(), shows: window.__vfTrace.shows.slice({seen})}})'))
                mid = (before + time.monotonic()) / 2
                polls.append(time.monotonic() - before)
                for s in data['shows']:
                    seen += 1
                    py_show = mid + (s['t'] - data['now']) / 1000.0
                    if prev_container == 'image' and s['container'] == 'web' and s['pane'] in (None, 1):
                        idx = len(transitions) + 1
                        transitions.append({'idx': idx, 'py': py_show, 'src': short(s['src'])})
                        for off in offsets:
                            pending.append({'due': py_show + off, 'off': off, 'idx': idx, 'py': py_show})
                    prev_container = s['container']
                for shot in sorted([p for p in pending if time.monotonic() >= p['due']], key=lambda p: p['due']):
                    pending.remove(shot)
                    if args.no_screenshots:
                        continue
                    t_cap = time.monotonic()
                    res = await cdp.call('Page.captureScreenshot', format='png')
                    name = out / f'{args.prefix}-{shot["idx"]:02d}-{shot["off"]:g}s.png'
                    name.write_bytes(base64.b64decode(res['data']))
                    print(f'shot  transition {shot["idx"]:02d} +{shot["off"]:g}s taken at +{t_cap - shot["py"]:.3f}s -> {name}')
                await asyncio.sleep(0.05)
            log_json = json.loads(await cdp.eval('JSON.stringify(window.__vfTrace.log)'))
            stats = json.loads(await cdp.eval(
                'JSON.stringify({ticks: window.__vfTrace.ticks, gaps: window.__vfTrace.gaps})'))
            await cdp.eval('window.__vfTrace.stop(); delete window.__vfTrace; "stopped"')
    finally:
        if tunnel is not None:
            close_tunnel(tunnel)

    events = [e for e in log_json]
    (out / f'{args.prefix}-log.json').write_text(json.dumps({'t0': t0, 'events': events, 'ticks': stats['ticks'],
                                                             'gaps': stats['gaps']}, indent=1))
    print()
    print('timeline (page clock, s from install; only changes)')
    last = None
    for e in events:
        t = (e['t'] - t0) / 1000.0
        if e['ev'] == 'state':
            st = e['st'][0]
            line = (f"shown={','.join(st['shown']) or '-'} web={'V' if st['webVisible'] else '-'} "
                    f"webSrc={short(st['webSrc'])} loaded={short(st['webLoaded'])}")
            if line == last:
                continue
            last = line
            print(f'{t:9.3f}  state  {line}')
        elif e['ev'] in ('show', 'show-done', 'show-error', 'web-load'):
            print(f"{t:9.3f}  {e['ev']:<10} container={e.get('container', '-')} src={short(e.get('src'))}"
                  + (f" error={e['error']}" if 'error' in e else ''))
    print()
    gaps = stats['gaps']
    if gaps:
        print(f"rAF interval ms: n={len(gaps)} median={statistics.median(gaps)} "
              f"p95={sorted(gaps)[int(len(gaps) * 0.95)]} max={max(gaps)}")
    print(f"ticks raf={stats['ticks']['raf']} int={stats['ticks']['int']}; DevTools round trip ms: "
          f"median={statistics.median(polls) * 1000:.0f} max={max(polls) * 1000:.0f}")
    for row in analyse(events, t0):
        print('transition', row)
    print(f'log: {out / (args.prefix + "-log.json")}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', help='node to tunnel to (not with --direct)')
    ap.add_argument('--user', default='admin')
    ap.add_argument('--password-file', help='login of the ssh tunnel (not with --direct)')
    ap.add_argument('--direct', action='store_true', help='run on the node: DevTools at 127.0.0.1:9223, no tunnel')
    ap.add_argument('--window', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=70)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--prefix', default='viewer-swap')
    ap.add_argument('--offsets', default='0.3,1,2,4', help='screenshot offsets after each image -> web show(), seconds')
    ap.add_argument('--local-port', type=int, default=0, help='local end of the tunnel (default: a free port)')
    ap.add_argument('--lib', type=Path, default=DEFAULT_LIB, help='deploy/script/lib.sh')
    ap.add_argument('--no-screenshots', action='store_true')
    args = ap.parse_args()
    if not args.direct and not (args.host and args.password_file):
        ap.error('--host and --password-file are required unless --direct')
    return asyncio.run(run(args))


if __name__ == '__main__':
    sys.exit(main())
