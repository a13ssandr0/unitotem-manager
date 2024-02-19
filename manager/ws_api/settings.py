from typing import Optional

from utils.ws.endpoints import WSAPIBase
from utils.models import Config
from utils.audio import setDefaultAudioDevice, getAudioDevices, setVolume, setMute


class Settings(WSAPIBase):
    async def default_duration(self, duration: Optional[int] = None):
        if duration is not None:
            Config.def_duration = duration
            Config.save()
        await self.ws.broadcast('settings/default_duration', duration=Config.def_duration)

    class Audio(WSAPIBase):
        async def default(self, device: Optional[str] = None):
            if device is not None:
                setDefaultAudioDevice(device)
            await self.ws.broadcast('settings/audio/devices', devices=getAudioDevices())

        async def devices(self):
            await self.ws.broadcast('settings/audio/devices', devices=getAudioDevices())

        async def volume(self, device: Optional[str] = None, volume: Optional[float] = None):
            if volume is not None:
                setVolume(device, volume)
            await self.ws.broadcast('settings/audio/devices', devices=getAudioDevices())

        async def mute(self, device: Optional[str] = None, mute: Optional[bool] = None):
            if mute is not None:
                setMute(device, mute)
            await self.ws.broadcast('settings/audio/devices', devices=getAudioDevices())

    class Display(WSAPIBase):
        async def getBounds(self):
            await self.ws.broadcast('settings/display/getBounds', **WINDOW['bounds'])

        async def setBounds(self, x: int, y: int, width: int, height: int):
            await self.ui_ws.broadcast('setBounds', x=x, y=y, width=width, height=height)

        async def getOrientation(self):
            await self.ws.broadcast('settings/display/getOrientation', orientation=WINDOW['orientation'])

        async def setOrientation(self, orientation: int):
            await self.ui_ws.broadcast('setOrientation', orientation=orientation)

        async def getFlip(self):
            await self.ws.broadcast('settings/display/getFlip', flip=WINDOW['flip'])

        async def setFlip(self, flip: int):
            await self.ui_ws.broadcast('setFlip', flip=flip)

    class Remote(WSAPIBase):
        async def getMode(self):
            await self.ws.broadcast('settings/remote/get',
                       remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                       remote_connected=REMOTE_CONNECTED,
                       remote_port=Config.remote_server_port,
                       remote_clients=list(Config.remote_clients.items()))

        async def setMode(self, remote_server: Optional[IPv4Address],
                                  remote_port: Optional[PositiveInt] = const.default_port_secure):
            remote_port = remote_port or const.default_port_secure
            if Config.remote_server_ip == remote_server and Config.remote_server_port == remote_port:
                return
            Config.remote_server_ip = remote_server
            Config.remote_server_port = remote_port
            Config.remote_server_pk = None
            Config.save()
            for task in asyncio.all_tasks():
                if task.get_name() in ['page_controller', 'remote_control']:
                    task.cancel()
            if remote_server:
                # noinspection PyAsyncCall
                asyncio.create_task(connect_to_server(remote_server, remote_port), name='remote_control')
            else:
                # noinspection PyAsyncCall
                asyncio.create_task(webview_control_main(), name='page_controller')
            await self.ws.broadcast('settings/remote/get',
                               remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                               remote_connected=REMOTE_CONNECTED,
                               remote_port=Config.remote_server_port,
                               remote_clients=list(Config.remote_clients.items()))

        async def disconnect(self, client: str):
            for remote in self.remote_ws.active_connections:
                if remote.headers['instance_id'] == client:
                    await remote.close(code=4023, reason="Server forced disconnection")
                    self.remote_ws.disconnect(remote)
                    del Config.remote_clients[remote.headers['instance_id']]
                    await self.ws.broadcast('settings/remote/get',
                                       remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                                       remote_connected=REMOTE_CONNECTED,
                                       remote_port=Config.remote_server_port,
                                       remote_clients=list(Config.remote_clients.items()))

    async def hostname(self, hostname: Optional[str] = None):
        if hostname is not None:
            set_hostname(hostname)
        await self.ws.broadcast('settings/hostname', hostname=get_hostname())

    async def get_wifis(self):
        await self.ws.broadcast('settings/get_wifis', wifis=get_wifis())

    class Netplan(WSAPIBase):
        async def newFile(self, filename: str):
            create_netplan(filename)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

        async def getFile(self, filename: Optional[str] = None):
            netplan_files = get_netplan_file_list()
            if filename is not None:
                if filename in netplan_files:
                    await self.ws.broadcast('settings/netplan/file/get', files={filename: get_netplan_file(filename)})
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in netplan_files})

        async def changeFile(self, ws: WebSocket, filename: Optional[str] = None, content: str = '', apply: bool = True):
            global DEFAULT_AP
            if filename is not None:
                res = set_netplan(secure_filename(filename), content, apply)
            else:
                res = generate_netplan(apply)
            # noinspection PySimplifyBooleanCheck
            if res is True:
                if DEFAULT_AP and do_ip_addr(True):  # AP is still enabled, but now we are connected, AP is no longer needed
                    stop_hostpot()
                    DEFAULT_AP = None
                    Config.assets.next_a()
            elif isinstance(res, str):
                await self.ws.send(ws, 'error', error='Netplan error', extra=res)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

        async def deleteFile(self, ws: WebSocket, filename: Optional[str] = None, apply: bool = True):
            res = del_netplan_file(filename, apply)
            if isinstance(res, str):
                await self.ws.send(ws, 'error', error='Netplan error', extra=res)
            await self.ws.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

