import asyncio
from base64 import b64decode
from ipaddress import IPv4Address
from json import loads
from socket import gethostname
from traceback import format_exc
from typing import Optional

import asyncwebsockets
import requests
from Crypto.Hash import SHA256
from Crypto.Signature import pss as PSS
from loguru import logger
from pydantic import PositiveInt
from wsproto.events import CloseConnection

from api.commons import SHUTDOWN_EVENT
from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.environment import environ
from utils.models.assets import assets_manager
from utils.models.command_line import cmdargs
from utils.models.remote import RemoteManager
from webview_controller.controller import Controller



class Remote(WSAPIBase):
    __remote_connected = False

    def __init__(self, ws, remote_ws):
        super().__init__(ws, remote_ws)
        self.controller = Controller.get_instance()
        self.remote_manager = RemoteManager.get_instance()

    def getMode(self):
        return WSBroadcast(
                remote_server=self.remote_manager.server_ip.compressed if self.remote_manager.server_ip else None,
                remote_connected=self.__remote_connected,
                remote_port=self.remote_manager.server_port,
                remote_clients=self.remote_manager.clients_list,
        )

    def setMode(self, remote_server: Optional[IPv4Address],
                remote_port: Optional[PositiveInt] = cmdargs.port_secure):
        remote_port = remote_port or cmdargs.port_secure
        if self.remote_manager.server_ip == remote_server and self.remote_manager.server_port == remote_port:
            return None
        self.remote_manager.server_ip = remote_server
        self.remote_manager.server_port = remote_port
        self.remote_manager.server_pubk = None
        self.remote_manager.save()
        for task in asyncio.all_tasks():
            if task.get_name() in ['page_controller', 'remote_control']:
                task.cancel()
        if remote_server:
            # noinspection PyAsyncCall
            asyncio.create_task(self.__connect_to_server(remote_server, remote_port), name='remote_control')
        else:
            # noinspection PyAsyncCall
            asyncio.create_task(self.__webview_control_main(), name='page_controller')
        return self.getMode()

    async def disconnect(self, client: str):
        for remote in self.remote_ws.active_connections:
            if remote.headers['instance_id'] == client:
                await remote.close(code=4023, reason="Server forced disconnection")
                self.remote_ws.disconnect(remote)
                del self.remote_manager.clients[remote.headers['instance_id']]
                self.remote_manager.save()
                break
        return self.getMode()

    async def __webview_control_main(self):
        logger.info('Starting webview controller')
        async for asset in assets_manager.iter_wait():
            await self.ws.broadcast('Scheduler/Asset/current', uuid=asset.uuid)
            url = asset.url
            if url.startswith('file:'):
                url = 'https://localhost/uploaded/' + url.removeprefix('file:')
            data = dict(
                    src=url,
                    container=asset.media_type + 1,  # [None, 'web', 'image', 'video', 'audio'][asset.media_type + 1],
                    fit=asset.fit,  # ['contain', 'cover', 'fill'][asset.fit],
                    bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else 'rgb(0,0,0)'
            )
            # self.controller.Show(**data)
            await self.remote_ws.broadcast('Show', False, **data)

    async def __connect_to_server(self, ip: IPv4Address, port: PositiveInt = cmdargs.port_secure, headers=None):
        if headers is None:
            headers = {}
        url = f'wss://{ip}:{port}/remote'
        headers.setdefault("instance_id", environ.instance_id)
        headers.setdefault("hostname", gethostname())
        headers.setdefault("port", cmdargs.port_secure)
        while not SHUTDOWN_EVENT.is_set():
            try:
                logger.info('Connecting to', url)
                if self.remote_manager.server_pubk is None:
                    server_pk = requests.get(f'https://{ip}:{port}/remote/public_key', verify=False).content
                    self.remote_manager.server_pubk = server_pk
                    self.remote_manager.save()

                verifier = PSS.new(self.remote_manager.server_pubk)
                # noinspection PyArgumentList
                async with asyncwebsockets.open_websocket(url, list(headers.items())) as ws:
                    self.__remote_connected = True
                    logger.success('Connected to', url)
                    while not SHUTDOWN_EVENT.is_set():
                        msg = await ws._next_event()
                        if isinstance(msg, CloseConnection):
                            if msg.code == 4023:  # Server forced disconnection for unpairing
                                self.__remote_connected = False
                                # TODO: this should be broadcast
                                self.setMode(remote_server=None, remote_port=None)
                                return
                            break
                        data, signature = getattr(msg, 'data', '.').split('.')
                        data, signature = b64decode(data), b64decode(signature)
                        verifier.verify(SHA256.new(data), signature)
                        data = loads(data)
                        if data.pop('target') == 'Show':
                            self.controller.Show(**data)
            except asyncio.exceptions.CancelledError:
                logger.info('Disconnected from remote server')
                break
            except ValueError:
                logger.error('Invalid signature, disconnected from server')
                await asyncio.sleep(5)
            except OSError as e:
                if e.args[0] == 'All connection attempts failed':
                    logger.warning('Server unavailable, retrying in 5 seconds...')
                else:
                    logger.error(format_exc())
                await asyncio.sleep(5)
            except Exception:
                logger.error(format_exc())
            self.__remote_connected = False
