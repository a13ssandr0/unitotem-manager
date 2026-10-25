#!/usr/bin/env python3
"""
Runs ON the board: a Chromium trace (CDP Tracing on the browser target of port 9223) while the ticker crawls, optionally with
the page's MAIN THREAD BLOCKED on purpose (a 300 ms busy loop every second, injected into the window's page). Counts the frames
the display compositor drew per second and the gaps between them, and how much rasterization / paint / layout the main thread
did meanwhile. A crawl moved by the compositor keeps its frame rate whatever the main thread does.

    board-trace.py SECONDS LABEL [block] [WINDOW]

Prints one JSON line with the numbers.
"""
import asyncio
import collections
import json
import statistics
import sys
import urllib.request

import websockets

CATEGORIES = ['cc', 'viz', 'gpu', 'benchmark', 'disabled-by-default-devtools.timeline.frame', 'devtools.timeline', 'toplevel']


def targets(window):
    version = json.load(urllib.request.urlopen('http://127.0.0.1:9223/json/version'))['webSocketDebuggerUrl']
    page = next(t['webSocketDebuggerUrl'] for t in json.load(urllib.request.urlopen('http://127.0.0.1:9223/json/list'))
                if t.get('type') == 'page' and f'window={window}' in t.get('url', ''))
    return version, page


class Conn:
    def __init__(self, ws):
        self.ws, self.n, self.events, self.waiters = ws, 0, [], {}

    async def pump(self, sink=None):
        async for raw in self.ws:
            msg = json.loads(raw)
            if 'id' in msg and msg['id'] in self.waiters:
                self.waiters.pop(msg['id']).set_result(msg)
            elif sink is not None:
                sink(msg)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.n] = fut
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        return (await fut).get('result', {})


async def main():
    seconds, label = float(sys.argv[1]), sys.argv[2]
    block = 'block' in sys.argv[3:]
    window = next((a for a in sys.argv[3:] if a.isdigit()), '0')
    browser_url, page_url = targets(window)
    trace, done = [], asyncio.Event()

    def sink(msg):
        if msg.get('method') == 'Tracing.dataCollected':
            trace.extend(msg['params']['value'])
        elif msg.get('method') == 'Tracing.tracingComplete':
            done.set()

    async with websockets.connect(browser_url, max_size=None) as bws, websockets.connect(page_url, max_size=None) as pws:
        b, p = Conn(bws), Conn(pws)
        tb = asyncio.create_task(b.pump(sink))
        tp = asyncio.create_task(p.pump())
        await b.call('Tracing.start', traceConfig={'includedCategories': CATEGORIES, 'recordMode': 'recordContinuously'})
        if block:
            await p.call('Runtime.evaluate', expression='window.__blk = setInterval(() => { const e = performance.now() + 300; while (performance.now() < e); }, 1000)')
        await asyncio.sleep(seconds)
        if block:
            await p.call('Runtime.evaluate', expression='clearInterval(window.__blk)')
        await b.call('Tracing.end')
        await asyncio.wait_for(done.wait(), 60)
        tb.cancel(); tp.cancel()

    ts = [e['ts'] for e in trace if 'ts' in e and e.get('ph') != 'M']
    t0, t1 = min(ts) + 1_000_000, max(ts)       # drop the first second (start-up of the trace)
    ev = [e for e in trace if e.get('ph') != 'M' and t0 <= e.get('ts', 0) <= t1]
    span = (t1 - t0) / 1e6
    names = collections.Counter(e['name'] for e in ev)

    def per_second(*wanted):
        return sum(names[n] for n in wanted) / span

    swaps = sorted(e['ts'] for e in ev if e['name'] == 'Display::DrawAndSwap')
    gaps = [(b - a) / 1000 for a, b in zip(swaps, swaps[1:])]
    long_main = [e for e in ev if e['name'] == 'ThreadControllerImpl::RunTask' and e.get('dur', 0) > 100_000]
    out = {
        'label': label, 'blocked_main_thread': block, 'seconds': round(span, 1),
        'frames_drawn_per_s': round(len(swaps) / span, 1),
        'gap_ms': {'median': round(statistics.median(gaps), 2), 'p95': round(sorted(gaps)[int(len(gaps) * .95)], 2), 'max': round(max(gaps), 2)} if gaps else None,
        'gaps_over_1.5x_median': sum(1 for g in gaps if gaps and g > 1.5 * statistics.median(gaps)),
        'main_thread_tasks_over_100ms': len(long_main),
        'raster_tasks_per_s': round(per_second('RasterTask', 'RasterizerTaskImpl::RunOnWorkerThread'), 2),
        'paint_per_s': round(per_second('Paint', 'PaintLayer', 'UpdateLayer', 'Layerize', 'PrePaint'), 2),
        'layout_per_s': round(per_second('Layout', 'UpdateLayoutTree'), 2),
        'commits_per_s': round(per_second('ProxyMain::BeginMainFrame', 'LayerTreeHostImpl::CommitComplete'), 2),
        'related_event_names': {n: c for n, c in names.most_common() if any(k in n for k in ('Display::DrawAndSwap', 'RasterTask', 'PrePaint', 'UpdateLayoutTree', 'BeginMainFrameAborted', 'ProxyMain::BeginMainFrame', 'AnimationFrame'))},
    }
    commits = sorted(e['ts'] for e in ev if e['name'] == 'ProxyMain::BeginMainFrame::commit')
    cg = [(b - a) / 1000 for a, b in zip(commits, commits[1:])]
    # a commit is the main thread putting new content on screen: a crawl moved by the main thread only moves when it commits
    out['main_thread_commits_per_s'] = round(len(commits) / span, 1)
    out['max_gap_between_commits_ms'] = round(max(cg), 1) if cg else None
    out['draw_and_swap_phases'] = dict(collections.Counter(f"{e.get('ph')}:{e.get('pid')}" for e in ev if e['name'] == 'Display::DrawAndSwap'))
    out['gap_histogram_ms'] = dict(sorted(collections.Counter(int(g // 2) * 2 for g in gaps).items()))
    print(json.dumps(out))


asyncio.run(main())
