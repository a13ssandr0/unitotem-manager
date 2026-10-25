#!/usr/bin/env python3
"""
Drives the REAL ticker on a board with a real monitor (run from the dev PC): configures sources and the window through
the websocket API like the web UI does, and takes real pixels of the viewer's page with board-cdp.py running on the board.

    .venv/bin/python tools/ticker/venus-run.py --host 192.168.1.128 --feeds http://192.168.1.50:8765 --out DIR [--only NAME,...]

Needs tools/ticker/board-cdp.py and board-cpu.py copied to /var/lib/unitotem/ticker-test on the board (deploy-board.sh does
not do it: scp them) and a feed server running (tools/ticker/feedserver.py). Every ssh call has SSH_ASKPASS_REQUIRE=never.
"""
import argparse
import asyncio
import json
import os
import ssl
import subprocess
import sys
import time

import httpx
import websockets

SSH = ['env', '-u', 'DISPLAY', '-u', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE=never', 'sshpass', '-p', 'unitotem']
OPTS = ['-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no', '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'StrictHostKeyChecking=no', '-o', 'LogLevel=ERROR']
BOARD = '/var/lib/unitotem/ticker-test'

BASE = {'enabled': True, 'position': 'bottom', 'mode': 'reserve', 'rows': 1, 'font_size': 3.0, 'font_unit': 'percent',
        'color': 'rgb(255,255,255)', 'background': 'rgba(0,0,0,0.85)', 'speed': 2.5, 'separator': '   •   ',
        'template': '[b]{time:HH:mm}[/b] - {title}', 'select': {'mode': 'count', 'count': 30, 'seconds': 7200},
        'clock': {'enabled': False, 'position': 'end', 'seconds': False, 'date': False, 'rows': 1}, 'locale': 'en-GB'}


class Api:
    def __init__(self, host):
        self.host, self.ws, self.frames, self.waiters = host, None, [], []

    async def __aenter__(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        async with httpx.AsyncClient(verify=False, follow_redirects=False) as http:
            r = await http.post(f'https://{self.host}/auth/token', data={'username': 'root', 'password': 'unitotem'})
            cookie = '; '.join(f'{k}={v}' for k, v in r.cookies.items())
        self.ws = await websockets.connect(f'wss://{self.host}/ws', ssl=ctx, additional_headers={'Cookie': cookie}, max_size=None)
        self.task = asyncio.create_task(self._pump())
        return self

    async def __aexit__(self, *a):
        self.task.cancel()
        await self.ws.close()

    async def _pump(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            self.frames.append(msg)
            for w in list(self.waiters):
                if w[0](msg):
                    w[1].set_result(msg)
                    self.waiters.remove(w)

    async def send(self, target, **args):
        await self.ws.send(json.dumps({'target': target, **args}))

    async def call(self, target, reply=None, timeout=15, **args):
        """Sends and waits for the next frame of `reply` (default: the same target)."""
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append((lambda m: m.get('target') == (reply or target), fut))
        await self.send(target, **args)
        return await asyncio.wait_for(fut, timeout)

    async def wait(self, pred, timeout=30):
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append((pred, fut))
        return await asyncio.wait_for(fut, timeout)


def ssh(host, cmd, timeout=120):
    return subprocess.run(SSH + ['ssh'] + OPTS + [f'root@{host}', cmd], capture_output=True, text=True, timeout=timeout).stdout.strip()


def scp_from(host, remote, local):
    subprocess.run(SSH + ['scp', '-O'] + OPTS + [f'root@{host}:{remote}', local], capture_output=True, check=True)


class Run:
    def __init__(self, args):
        self.args, self.out, self.results = args, args.out, []
        os.makedirs(self.out, exist_ok=True)

    def note(self, line):
        print(line, flush=True)
        self.results.append(line)

    def shot(self, name):
        remote = f'/tmp/ticker-{name}.png'
        ssh(self.args.host, f'python3 {BOARD}/board-cdp.py shot {remote}')
        scp_from(self.args.host, remote, os.path.join(self.out, name + '.png'))
        self.note(f'screenshot {name}.png')

    def inspect(self):
        raw = ssh(self.args.host, f'python3 {BOARD}/board-cdp.py inspect')
        try:
            return json.loads(json.loads(raw))
        except (ValueError, TypeError):
            return {'raw': raw}

    def cpu(self, label, seconds=20):
        line = ssh(self.args.host, f'python3 {BOARD}/board-cpu.py {seconds} "{label}"', timeout=seconds + 60)
        self.note('CPU ' + line)
        return line


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='192.168.1.128')
    ap.add_argument('--feeds', required=True, help='base URL of the feed server as seen from the board')
    ap.add_argument('--out', required=True)
    ap.add_argument('--only', default='')
    ap.add_argument('--public', action='store_true', help='also add the real public feeds')
    args = ap.parse_args()
    run = Run(args)
    only = set(filter(None, args.only.split(',')))

    async with Api(args.host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer_id = next(iter(views))
        window = views[viewer_id]['windows'][0]
        win_id = window['window_id']
        run.note(f'window {win_id} on {viewer_id}: {window.get("screen_name")} {window.get("width")}x{window.get("height")}')

        async def cfg(**over):
            settings = {**BASE, **over}
            await api.send('Ticker/set_settings', viewer_id=viewer_id, window_id=win_id, settings=settings)
            await asyncio.sleep(3)

        async def sources():
            return (await api.call('Ticker/sources'))['sources']

        async def clear_sources():
            for s in await sources():
                await api.send('Ticker/delete_source', id=s['id'])
            await asyncio.sleep(1)

        async def add(path_or_url, name='', interval=60):
            url = path_or_url if path_or_url.startswith('http') else args.feeds + path_or_url
            await api.send('Ticker/add_source', url=url, name=name, interval=interval)

        async def wait_fetched(n, timeout=40):
            end = time.time() + timeout
            while time.time() < end:
                s = await sources()
                if len(s) >= n and all(not x['fetching'] and (x['last_ok'] or x['error']) for x in s):
                    return s
                await asyncio.sleep(1)
            return await sources()

        def want(name):
            return not only or name in only

        # ── sources ──────────────────────────────────────────────────────────
        if want('sources'):
            await clear_sources()
            await cfg(enabled=False)
            await add('/live.xml', 'Live', 60)
            await add('/atom.xml', 'Atom', 300)
            await add('/hostile.xml', 'Hostile', 600)
            await add('/dateless.xml', 'Dateless', 600)
            if args.public:
                await add('https://feeds.bbci.co.uk/news/rss.xml', 'BBC', 900)
                await add('https://www.ansa.it/sito/ansait_rss.xml', 'ANSA', 900)
                await add('https://www.theguardian.com/world/rss', 'Guardian', 900)
                await add('https://www.repubblica.it/rss/homepage/rss2.0.xml', 'Repubblica', 900)
            s = await wait_fetched(8 if args.public else 4)
            for x in s:
                run.note(f"source {x['label']:12} items={x['items']:4} error={x['error']!r} interval={x['interval']}")
            merged = (await api.call('Ticker/preview'))['items']
            run.note(f'merged list: {len(merged)} headlines, sources {sorted({m["source"] for m in merged})}')

        # ── bar variants ─────────────────────────────────────────────────────
        if want('bar'):
            await cfg()
            run.shot('01-one-row-reserve-bottom')
            run.note('inspect ' + json.dumps(run.inspect()))
            await cfg(rows=2, mode='overlay')
            run.shot('02-two-rows-overlay-bottom')
            await cfg(rows=1, mode='reserve', position='top', font_size=4.0, color='rgb(255,235,59)', background='rgba(13,27,62,0.95)')
            run.shot('03-top-reserve-yellow-on-blue-4pct')
            await cfg(rows=2, mode='overlay', position='top', font_size=2.0, color='#ffffff', background='rgba(180,0,0,0.6)')
            run.shot('04-top-overlay-two-rows-red-translucent-2pct')
            await cfg(font_size=64, font_unit='px', rows=1)
            run.shot('05-64px-text')

        if want('template'):
            await cfg(template='[bold black on yellow] {source!u} [/] {time:dd MMM HH:mm} [cyan]{title:.90}[/] [dim italic]{summary:.60}[/]',
                      font_size=3.5)
            run.shot('06-template-rich-and-fields')
            await cfg(template='{time:%d/%m/%Y %H:%M} [underline]{title}[/underline] [strike]x[/strike]', font_size=3.5)
            run.shot('07-template-python-time-format')
            await cfg(template='[nope]{title}')
            run.shot('08-template-error-falls-back-to-titles')
            await cfg(template='[green]{title}[/]', locale='it-IT')
            run.shot('09-hostile-feed-text-only')

        if want('clock'):
            clock = {'enabled': True, 'position': 'start', 'seconds': True, 'date': True, 'rows': 1}
            await cfg(clock=clock)
            run.shot('10-clock-start-seconds-date-1row')
            await cfg(clock={**clock, 'position': 'end', 'seconds': False, 'rows': 2})
            run.shot('11-clock-end-date-2rows-no-seconds')
            await cfg(clock={**clock, 'position': 'end', 'seconds': False, 'date': False})
            run.shot('12-clock-end-time-only')
            await cfg(rows=2, clock={**clock, 'position': 'start', 'seconds': True, 'date': True, 'rows': 2})
            run.shot('13-two-lanes-with-two-row-clock-start')

        if want('selection'):
            await clear_sources()
            await add('/dated.xml', 'Dated', 60)
            await wait_fetched(1)
            tpl = '{time:ddd HH:mm}  {title:.40}'
            for name, sel in (('count5', {'mode': 'count', 'count': 5, 'seconds': 7200}), ('today', {'mode': 'today', 'count': 5, 'seconds': 7200}),
                              ('range2h', {'mode': 'range', 'count': 5, 'seconds': 7200}), ('range2d', {'mode': 'range', 'count': 5, 'seconds': 172800})):
                await cfg(select=sel, template=tpl, speed=0.5, font_size=3.0)
                st = run.inspect()
                local_h = int(ssh(args.host, 'date +%H')) + int(ssh(args.host, 'date +%M')) / 60
                run.note(f'selection {name}: selected={st.get("selected")} of {st.get("items")} (board local time {local_h:.1f} h)')
                run.shot(f'14-selection-{name}')

        # ── 500 headlines and CPU ────────────────────────────────────────────
        if want('cpu'):
            await clear_sources()
            await add('/big.xml', 'Big', 3600)
            await wait_fetched(1)
            big = (await api.call('Ticker/preview'))['items']
            run.note(f'big feed: {len(big)} headlines in the merged list')
            sel = {'mode': 'count', 'count': 500, 'seconds': 7200}
            await cfg(enabled=False)
            await asyncio.sleep(5)
            run.cpu('bar OFF (viewer idle page)')
            await cfg(select=sel, rows=1, speed=2.5, font_size=3.0)
            st = run.inspect()
            run.note('inspect (1 lane, 500) ' + json.dumps(st))
            run.cpu('1 lane, 500 headlines, 2.5 em/s')
            await cfg(select=sel, rows=2, speed=2.5, font_size=3.0)
            run.note('inspect (2 lanes, 500) ' + json.dumps(run.inspect()))
            run.cpu('2 lanes, 500 headlines, 2.5 em/s')
            await cfg(select=sel, rows=2, speed=2.5, font_size=3.0, clock={'enabled': True, 'position': 'end', 'seconds': True, 'date': True, 'rows': 2})
            run.cpu('2 lanes + clock with seconds')
            await cfg(select=sel, rows=1, speed=8, font_size=3.0)
            run.note('inspect (1 lane, 8 em/s) ' + json.dumps(run.inspect()))
            run.cpu('1 lane, 500 headlines, 8 em/s (fast)')
            run.shot('15-500-headlines-two-lanes')

        open(os.path.join(args.out, 'notes.txt'), 'w').write('\n'.join(run.results) + '\n')


if __name__ == '__main__':
    asyncio.run(main())
