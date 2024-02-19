from typing import Optional

from utils.models import Config
from utils.ws.endpoints import WSAPIBase

REMOTE_CONNECTED = False


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
