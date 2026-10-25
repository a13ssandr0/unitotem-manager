#!/usr/bin/env python3
"""
CPU of the whole viewer tree for a list of bar settings on one board (which knob makes the crawl cheaper?).

    knobs.py HOST FEEDS_URL 'label={json overrides}' ...
"""
import asyncio
import importlib.util
import json
import os
import sys

spec = importlib.util.spec_from_file_location('venus_run', os.path.join(os.path.dirname(__file__), 'venus-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


async def main():
    host, feeds, *cases = sys.argv[1:]
    async with vr.Api(host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer = next(iter(views))
        win = views[viewer]['windows'][0]['window_id']
        for s in (await api.call('Ticker/sources'))['sources']:
            await api.send('Ticker/delete_source', id=s['id'])
        await api.send('Ticker/add_source', url=feeds + '/big.xml', name='Big', interval=3600)
        await asyncio.sleep(8)
        for case in cases:
            label, _, raw = case.partition('=')
            settings = {**vr.BASE, 'select': {'mode': 'count', 'count': 500, 'seconds': 7200}, **json.loads(raw or '{}')}
            await api.send('Ticker/set_settings', viewer_id=viewer, window_id=win, settings=settings)
            await asyncio.sleep(5)
            print(vr.ssh(host, f'python3 {vr.BOARD}/board-cpu.py 15 "{label}"', timeout=90), flush=True)


asyncio.run(main())
