#!/usr/bin/env python3
"""
Puts a quiet static page (feedserver.py /black.html) on the first window of a board, so a measurement is not polluted by the animated
welcome page of a board that has no administrator yet (it repaints every frame: ~85 % of a core on a Pi 4).

    board-static-page.py HOST FEEDS_URL on      # adds an asset to the Default playlist, enables it, assigns the playlist
    board-static-page.py HOST FEEDS_URL off     # unassigns, deletes the asset
"""
import asyncio
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location('venus_run', os.path.join(os.path.dirname(__file__), 'venus-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)
NAME = 'ticker-test-static-page'


async def main():
    host, feeds, what = sys.argv[1:4]
    async with vr.Api(host) as api:
        views = (await api.call('Viewers/list'))['viewers']
        viewer = next(iter(views))
        win = views[viewer]['windows'][0]['window_id']
        playlist = (await api.call('Scheduler/playlists'))['playlists'][0]['playlist_id']
        assets = (await api.call('Scheduler/assets', playlist_id=playlist)).get('items', [])
        mine = [a for a in assets if a.get('name') == NAME]
        if what == 'on':
            if not mine:
                await api.send('Scheduler/add_url_assets', items=[{'url': feeds + '/black.html', 'name': NAME, 'duration': 3600, 'media_type': 0}], playlist_id=playlist)
                await asyncio.sleep(2)
                assets = (await api.call('Scheduler/assets', playlist_id=playlist)).get('items', [])
                mine = [a for a in assets if a.get('name') == NAME]
            await api.send('Scheduler/edit_asset', uuid=mine[0]['uuid'], playlist_id=playlist, enabled=True, duration=3600)
            await asyncio.sleep(1)
            await api.send('Viewers/assign_playlist', viewer_id=viewer, window_id=win, playlist_id=playlist)
        else:
            await api.send('Viewers/unassign_playlist', viewer_id=viewer, window_id=win)
            for a in mine:
                await api.send('Scheduler/delete_asset', uuid=a['uuid'], playlist_id=playlist)
        await asyncio.sleep(2)
        print('ok', what, len(mine))


asyncio.run(main())
