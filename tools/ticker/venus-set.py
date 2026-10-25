#!/usr/bin/env python3
"""
Sets the bar of the first window of a board and/or its sources from the command line (uses the Api client of venus-run.py).

    venus-set.py HOST --bar '{"enabled": false}'                      # merged over the defaults of venus-run.py
    venus-set.py HOST --feeds URL --source /big.xml                  # replaces every source by these (paths on --feeds, or full URLs)
"""
import argparse
import asyncio
import importlib.util
import json
import os

spec = importlib.util.spec_from_file_location('venus_run', os.path.join(os.path.dirname(__file__), 'venus-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('host')
    ap.add_argument('--bar')
    ap.add_argument('--feeds', default='')
    ap.add_argument('--source', action='append', default=[])
    args = ap.parse_args()
    async with vr.Api(args.host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer = next(iter(views))
        win = views[viewer]['windows'][0]['window_id']
        if args.source:
            for s in (await api.call('Ticker/sources'))['sources']:
                await api.send('Ticker/delete_source', id=s['id'])
            for src in args.source:
                url = src if src.startswith('http') else args.feeds + src
                await api.send('Ticker/add_source', url=url, name='', interval=3600)
            await asyncio.sleep(8)
        if args.bar:
            await api.send('Ticker/set_settings', viewer_id=viewer, window_id=win, settings={**vr.BASE, **json.loads(args.bar)})
            await asyncio.sleep(2)
        print('ok', viewer, win)


asyncio.run(main())
