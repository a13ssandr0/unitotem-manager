__all__ = ['WSManager']

from base64 import b64encode
from functools import wraps
from inspect import isawaitable, isclass, isgeneratorfunction, iscoroutinefunction
from json import dumps
from os.path import join
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import WebSocket
from pydantic import validate_call


class WSManager:
    pk: Optional[RSAPrivateKey] = None

    def __init__(self, cache_last=False):
        self.active_connections: list[WebSocket] = []
        self.last = {} if cache_last else None
        # if last is not None we are using command cache.
        # this means every time a client connects will receive
        # the last command sent for each target.
        # this is needed for the viewer program that may connect after a command
        # was sent (i.e. the manager finishes starting before the viewer, or the 
        # viewer for whatever reason restarts)
        #
        # if we need caching, last is initialized to something different from None
        # this way we avoid using two variables: one for setting and the other
        # for actual caching

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.last is not None:
            for cmd in self.last.values():
                await websocket.send_text(cmd)

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
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
        target = target.lower()
        text = self.prepare_message({'target': target, **kwargs}, nocache=nocache)
        await websocket.send_text(text)

    async def broadcast(self, target: str, nocache=False, **kwargs):
        target = target.lower()
        text = self.prepare_message({'target': target, **kwargs}, nocache=nocache)
        for connection in self.active_connections:
            await connection.send_text(text)

    handlers = {}

    def add(self, target: str, **validator_kwargs):
        validator_kwargs.setdefault('arbitrary_types_allowed', True)

        def decorator(func):

            if WebSocket in func.__annotations__.values() or \
                    (callable(func) and func.__name__ == "<lambda>" and func.__code__.co_posonlyargcount):
                # lambda functions with websocket parameter must declare it as the
                # first positional only argument
                func = validate_call(func, config=validator_kwargs)  # type: ignore

                @wraps(func)
                async def wrapper(caller_ws, *args, **kwargs):  # type: ignore
                    f = func(caller_ws, *args, **kwargs)
                    if isawaitable(f):
                        return await f
                    else:
                        return f
            else:
                func = validate_call(func, config=validator_kwargs)  # type: ignore

                @wraps(func)
                async def wrapper(_, *args, **kwargs):
                    f = func(*args, **kwargs)
                    if isawaitable(f):
                        return await f
                    else:
                        return f

            self.handlers[target] = wrapper

            return wrapper

        return decorator


class WSAPIBase:

    def __init__(self, ws: WSManager, ui_ws: WSManager, remote_ws: WSManager):
        self.ws = ws
        self.ui_ws = ui_ws
        self.remote_ws = remote_ws


def api_props(*, allowed_users, allowed_roles, **validator_kwargs):
    validator_kwargs.setdefault('arbitrary_types_allowed', True)

    def decorator(func):
        func.allowed_users = allowed_users
        func.allowed_roles = allowed_roles
        func.validator_kwargs = validator_kwargs
        return func

    return decorator
