"""
Shared set-up of the rotation scripts: test page server on the board, a temporary playlist on the first window, the safety net
that puts the output back to normal, and the restore of everything at the end.

    async with Board(host, 'corners') as b:
        await b.set_content(r, flip)       # content turn, through the manager's API
        b.set_transform('270')             # output transform, through wlr-randr
"""
import asyncio
import importlib.util
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('venus_run', os.path.join(HERE, '..', '..', 'ticker', 'venus-run.py'))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

REMOTE = '/var/lib/unitotem/rot'
ENV = 'export XDG_RUNTIME_DIR=/run/unitotem/xdg WAYLAND_DISPLAY=wayland-0;'


class Board:
    def __init__(self, host, workload='idle'):
        self.host, self.workload = host, workload

    def ssh(self, cmd, timeout=120):
        return vr.ssh(self.host, cmd, timeout)

    def set_transform(self, transform):
        self.ssh(f'{ENV} wlr-randr --output {self.output} --transform {transform}')

    async def set_content(self, orientation, flip):
        await self.api.send('Settings/Display/set_orientation', viewer_id=self.viewer, window_id=self.win, orientation=orientation)
        await self.api.send('Settings/Display/set_flip', viewer_id=self.viewer, window_id=self.win, flip=flip)

    def set_workload(self, name):
        self.ssh(f'curl -s "localhost:8099/set?w={name}"')

    async def __aenter__(self):
        host = self.host
        self.output = json.loads(self.ssh(f'{ENV} wlr-randr --json'))[0]['name']
        self.ssh(f'mkdir -p {REMOTE}')
        subprocess.run(vr.SSH + ['scp', '-O'] + vr.OPTS + [os.path.join(HERE, 'rotsrv.py'), f'root@{host}:{REMOTE}/rotsrv.py'],
                       capture_output=True, check=True)
        self.ssh(f'kill $(cat {REMOTE}/pid) 2>/dev/null; nohup python3 {REMOTE}/rotsrv.py {REMOTE} >/dev/null 2>&1 & echo $! > {REMOTE}/pid;'
                 f' sleep 2; curl -s "localhost:8099/set?w={self.workload}"')
        self.net = self.ssh(f'{ENV} (sleep 3000; wlr-randr --output {self.output} --transform normal) >/dev/null 2>&1 & echo $!')
        self.api = await vr.Api(host).__aenter__()
        views = (await self.api.call('Viewers/list'))['viewers']
        self.viewer = next(iter(views))
        window = views[self.viewer]['windows'][0]
        self.win = window['window_id']
        self.before = dict(orientation=window.get('orientation', 0), flip=window.get('flip', 0), playlist=window.get('assigned_playlist'))
        reply = await self.api.call('Scheduler/create_playlist', reply='Scheduler/playlists', name='rotation-test')
        self.playlist = next(p['playlist_id'] for p in reply['playlists'] if p['name'] == 'rotation-test')
        await self.api.send('Scheduler/add_url_assets', items=[{'url': 'http://127.0.0.1:8099/run.html', 'name': 'rotation-test', 'duration': 3600,
                                                                'media_type': 0}], playlist_id=self.playlist)
        await asyncio.sleep(2)
        items = (await self.api.call('Scheduler/assets', playlist_id=self.playlist)).get('items', [])
        await self.api.send('Scheduler/edit_asset', uuid=items[0]['uuid'], playlist_id=self.playlist, enabled=True, duration=3600)
        await asyncio.sleep(1)
        await self.api.send('Viewers/assign_playlist', viewer_id=self.viewer, window_id=self.win, playlist_id=self.playlist)
        await asyncio.sleep(6)
        return self

    async def __aexit__(self, *exc):
        try:
            self.set_transform('normal')
            await self.set_content(self.before['orientation'], self.before['flip'])
            if self.before['playlist']:
                await self.api.send('Viewers/assign_playlist', viewer_id=self.viewer, window_id=self.win, playlist_id=self.before['playlist'])
            else:
                await self.api.send('Viewers/unassign_playlist', viewer_id=self.viewer, window_id=self.win)
            await asyncio.sleep(2)
            await self.api.send('Scheduler/delete_playlist', playlist_id=self.playlist)
            await asyncio.sleep(1)
        finally:
            self.ssh(f'pkill -P {self.net}; kill {self.net} $(cat {REMOTE}/pid); true')
            await self.api.__aexit__(*exc)
