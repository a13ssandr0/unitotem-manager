import asyncio
from base64 import b64decode
from ipaddress import IPv4Address
from json import loads
from socket import gethostname
from traceback import format_exc
from typing import Optional

import requests
from Cryptodome.Hash import SHA256
from Cryptodome.Signature import pss as PSS
from loguru import logger
from pydantic import PositiveInt
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from api.commons import SHUTDOWN_EVENT
from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSManager
from utils.environment import environ
from utils.models.command_line import cmdargs
from utils.models.remote import RemoteManager


class Remote(WSAPIBase):
    __remote_connected = False

    def __init__(self, ws, remote_ws):
        super().__init__(ws, remote_ws)
        try:
            self.remote_manager = RemoteManager.get_instance()
        except FileNotFoundError:
            # first boot: remote.json does not exist yet, start with defaults
            self.remote_manager = RemoteManager()

    def mode(self):
        return WSBroadcast(
                remote_server=self.remote_manager.server_ip.compressed if self.remote_manager.server_ip else None,
                remote_connected=self.__remote_connected,
                remote_port=self.remote_manager.server_port,
                remote_clients=self.remote_manager.clients_list,
        )

    def key_status(self):
        """State of the RSA signing key: missing, generating or ready"""
        return WSBroadcast(status=RemoteManager.signing_key_status())

    def set_mode(self, remote_server: Optional[IPv4Address],
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
        return self.mode()

    async def disconnect_client(self, client: str):
        for remote in self.remote_ws.active_connections:
            if remote.headers['instance_id'] == client:
                await remote.close(code=4023, reason="Server forced disconnection")
                self.remote_ws.disconnect(remote)
                del self.remote_manager.clients[remote.headers['instance_id']]
                self.remote_manager.save()
                break
        return self.mode()

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
                # Header values must be strings. The server's certificate is checked with the default rules for now;
                # authentication with the certificates exchanged at pairing time is still to be done.
                async with connect(url, additional_headers={k: str(v) for k, v in headers.items()}) as ws:
                    self.__remote_connected = True
                    logger.success('Connected to', url)
                    try:
                        async for msg in ws:
                            if SHUTDOWN_EVENT.is_set():
                                break
                            data, signature = (msg if isinstance(msg, str) else msg.decode()).split('.')
                            data, signature = b64decode(data), b64decode(signature)
                            verifier.verify(SHA256.new(data), signature)
                            data = loads(data)
                            target = data.pop('target')
                            if target == 'Show':
                                # Forward to locally connected Qt webview via WebSocket
                                await self.remote_ws.broadcast('Show', **data)
                    except ConnectionClosed as closed:
                        if closed.rcvd is not None and closed.rcvd.code == 4023:
                            self.__remote_connected = False
                            self.set_mode(remote_server=None, remote_port=None)
                            return
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
