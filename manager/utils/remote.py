import asyncio
import base64
from ipaddress import IPv4Address
from json import loads
from os import environ
from platform import node as get_hostname
from traceback import print_exc
from typing import Optional, cast

import asyncwebsockets
import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import PositiveInt
from wsproto.events import CloseConnection

import utils.constants as const
from utils.commons import SHUTDOWN_EVENT
from utils.ws.wsmanager import WSManager
from utils.models import Config
from utils.ws.endpoints import WSAPIBase

REMOTE_CONNECTED = False


class Remote(WSAPIBase):
    async def getMode(self):
        await self.ws.broadcast('Settings/Remote/get',
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
            asyncio.create_task(self.__connect_to_server(remote_server, remote_port), name='remote_control')
        else:
            # noinspection PyAsyncCall
            asyncio.create_task(self.__webview_control_main(), name='page_controller')
        await self.getMode()

    async def disconnect(self, client: str):
        for remote in self.remote_ws.active_connections:
            if remote.headers['instance_id'] == client:
                await remote.close(code=4023, reason="Server forced disconnection")
                self.remote_ws.disconnect(remote)
                del Config.remote_clients[remote.headers['instance_id']]
                await self.getMode()

    async def __webview_control_main(self):
        print('Starting webview controller')
        async for asset in Config.assets.iter_wait(waiter=SHUTDOWN_EVENT):
            await self.ws.broadcast('Scheduler/Asset/current', uuid=asset.uuid)
            url = asset.url
            if url.startswith('file:'):
                url = 'https://localhost/uploaded/' + url.removeprefix('file:')
            data = dict(
                src=url,
                container=[None, 'web', 'image', 'video', 'audio'][asset.media_type + 1],
                fit=['contain', 'cover', 'fill'][asset.fit],
                bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else None
            )
            await self.ui_ws.broadcast('Show', False, **data)
            await self.remote_ws.broadcast('Show', False, **data)

    async def __connect_to_server(self, ip: IPv4Address, port: PositiveInt = const.default_port_secure, headers=None):
        if headers is None:
            headers = {}
        global REMOTE_CONNECTED
        url = f'wss://{ip}:{port}/remote'
        headers.setdefault("instance_id", environ['instance_id'])
        headers.setdefault("hostname", get_hostname())
        headers.setdefault("port", const.default_port_secure)
        while not SHUTDOWN_EVENT.is_set():
            # noinspection PyBroadException
            try:
                print('Connecting to', url)
                if Config.remote_server_pk is None:
                    server_pk = requests.get(f'https://{ip}:{port}/remote/public_key', verify=False).content
                    Config.remote_server_pk = cast(rsa.RSAPublicKey, serialization.load_pem_public_key(server_pk))
                    Config.save()
                # noinspection PyArgumentList
                async with asyncwebsockets.open_websocket(url, list(headers.items())) as ws:
                    REMOTE_CONNECTED = True
                    print('Connected to', url)
                    while True:
                        # noinspection PyProtectedMember
                        msg = await ws._next_event()
                        if isinstance(msg, CloseConnection):
                            if msg.code == 4023:  # Server forced disconnection for unpairing
                                REMOTE_CONNECTED = False
                                await self.setMode(remote_server=None, remote_port=None)
                                return
                            break
                        data = loads(getattr(msg, 'data', '{}'))
                        if 'target' in data and '__signature__' in data:
                            Config.remote_server_pk.verify(
                                base64.b64decode(data['__signature__'].encode()),
                                data['src'].encode(),
                                padding.PSS(
                                    mgf=padding.MGF1(hashes.SHA256()),
                                    salt_length=padding.PSS.MAX_LENGTH
                                ),
                                hashes.SHA256()
                            )
                        if data.pop('target') == 'Show':
                            await self.ui_ws.broadcast('Show', False, **data)
                        if SHUTDOWN_EVENT.is_set():
                            break
            except asyncio.exceptions.CancelledError:
                print('Disconnected from remote server')
                break
            except InvalidSignature:
                print('Invalid signature, disconnected from server')
                await asyncio.sleep(5)
            except OSError as e:
                if e.args[0] == 'All connection attempts failed':
                    print('Server unavailable, retrying in 5 seconds...')
                else:
                    print_exc()
                await asyncio.sleep(5)
            except Exception:
                print_exc()
            REMOTE_CONNECTED = False
