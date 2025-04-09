__all__ = ['WSManager', 'Context', 'WSAPIBase']

import dataclasses
from base64 import b64encode
from collections import defaultdict
from json import dumps
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import WebSocket


class WSManager:
    pk: Optional[RSAPrivateKey] = None
    active_connections: list[WebSocket] = []
    active_users: defaultdict[str, list[WebSocket]] = defaultdict(list)
    last: Optional[dict] = None

    def __init__(self, cache_last=False):
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
        if self.pk:
            msg['__signature__'] = b64encode(self.pk.sign(
                msg['src'].encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )).decode()
        text = dumps(msg)
        if self.last is not None and not nocache:
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
