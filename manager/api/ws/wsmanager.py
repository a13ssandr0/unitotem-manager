__all__ = ['WSManager', 'Context', 'WSAPIBase']

import dataclasses
from base64 import b64encode
from collections import defaultdict
from json import dumps
from typing import Optional

from Crypto.Hash import SHA256
from Crypto.Signature import pss as PSS
from fastapi import WebSocket

from utils.models.remote import RemoteManager


class WSManager:
    active_connections: list[WebSocket] = []
    active_users: defaultdict[str, list[WebSocket]] = defaultdict(list)
    last: Optional[dict] = None
    signer: Optional[PSS.PSS_SigScheme] = None

    def __init__(self, *, cache_last=False, sign_messages=False):
        if cache_last:
            self.last = {}
        # if last is not None we are using command cache.
        # this means every time a client connects will receive
        # the last command sent for each target.
        # this is needed for the viewer program that may connect after a command
        # was sent (i.e. the manager finishes starting before the viewer, or the 
        # viewer for whatever reason restarts)
        #
        # if we need caching, self.last is initialized to something different from None
        # this way we avoid using two variables: one for setting and the other
        # for actual caching

        if sign_messages:
            self.signer = PSS.new(RemoteManager.get_instance().rsa_prik)

    async def connect(self, websocket: WebSocket, user: Optional[str] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if user:
            self.active_users[user].append(websocket)
        if self.last is not None:
            for cmd in self.last.values():
                await websocket.send_text(cmd)

    def disconnect(self, websocket: WebSocket, user: Optional[str] = None):
        try:
            self.active_connections.remove(websocket)
            if user:
                self.active_users[user].remove(websocket)
        except ValueError:
            pass  # it's not necessary to crash if not present

    def prepare_message(self, msg: dict, nocache=False):
        text = dumps(msg).encode()
        if self.signer:
            text = b64encode(text) + b'.' + b64encode(self.signer.sign(SHA256.new(text)))
        text=text.decode()
        if self.last is not None and not nocache:
            # if cache is enabled (self.last is not None) and message is set to be cached (not nocache)
            self.last[msg['target']] = text
        return text

    async def send(self, websocket: WebSocket, target: str, nocache=False, **kwargs):
        text = self.prepare_message({'target': target, **kwargs}, nocache=nocache)
        await websocket.send_text(text)

    async def multicast(self, users: str|list[str], target: str, nocache=False, **kwargs):
        text = self.prepare_message({'target': target, **kwargs}, nocache=nocache)
        if isinstance(users, str):
            for websocket in self.active_users[users]:
                await websocket.send_text(text)
        else:
            for user in users:
                for websocket in self.active_users[user]:
                    await websocket.send_text(text)

    async def broadcast(self, target: str, nocache=False, **kwargs):
        text = self.prepare_message({'target': target, **kwargs}, nocache=nocache)
        for connection in self.active_connections:
            await connection.send_text(text)



class WSAPIMeta(type):
    def __getattr__(cls, item):
        pass
        # will make this syntax (`Settings.Audio.get_devices()`) work instead of requiring api['Settings/Audio/get_devices']()

class WSAPIBase:

    def __init__(self, ws: WSManager, remote_ws: WSManager):
        self.ws = ws
        self.remote_ws = remote_ws


@dataclasses.dataclass
class Context:
    username: str


def api_props(*, allowed_users, allowed_roles, **validator_kwargs):
    validator_kwargs.setdefault('arbitrary_types_allowed', True)

    def decorator(func):
        func.allowed_users = allowed_users
        func.allowed_roles = allowed_roles
        func.validator_kwargs = validator_kwargs
        return func

    return decorator
