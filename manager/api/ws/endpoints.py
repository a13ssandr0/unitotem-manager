import inspect
import types
from functools import wraps
from importlib import import_module
from inspect import isclass, isgeneratorfunction, iscoroutinefunction, isasyncgenfunction
from json import dumps
from os.path import join
from subprocess import run as cmd_run
from traceback import format_exc
from typing import Any

from benedict import benedict
from fastapi import APIRouter, WebSocketException, Request, status, WebSocketDisconnect
from fastapi import WebSocket
from loguru import logger
from pydantic import validate_call

import api.constants as const
from api.commons import UPLOADS
from api.models import Config, UserPerms, User
from utils.objs import dict_sort
from routers.login import LOGMAN, NotAuthenticatedException
from api.ws.wsmanager import Context
from .responses import WSBroadcast, WSResponse, WSMulticast
from .wsmanager import WSManager, WSAPIBase

router = APIRouter()
REMOTE_WS = WSManager(cache_last=True)
UI_WS = WSManager(cache_last=True)
WS = WSManager(cache_last=False)


# noinspection PyUnresolvedReferences
class WebSocketAPI:
    generators: dict[str, types.FunctionType] = {}

    def __init__(self, ws: WSManager, ui_ws: WSManager, remote_ws: WSManager):
        self.__ws = ws
        self.__ui_ws = ui_ws
        self.__remote_ws = remote_ws
        self.load_class(self.Power)

    @property
    def tree(self):
        items = benedict({k: (f.__doc__ or '').strip() for k, f in self.generators.items() if '/_' not in k}).unflatten('/')
        return dict_sort(items)

    def import_class(self, path: str, name: str, prefix: str = None):
        logger.debug(f'Importing {name} from {path}')
        cls = import_module(path).__getattribute__(name)
        self.load_class(cls, prefix=prefix)
        logger.info(f'Imported {name} from {path}')

    def load_class(self, cls: type, prefix: str = None):
        if not issubclass(cls, WSAPIBase):
            raise ValueError(f"Class {cls.__name__} is not a subclass of WSAPIBase")

        self.generators.update(self.__treegen(cls, prefix))

    # noinspection PyPep8Naming
    def __treegen(self, Cls: type, prefix: str = None):
        classname = Cls.__name__
        logger.debug("Class: " + classname)
        if prefix is None:
            prefix = classname
        else:
            prefix = join(prefix, classname)

        gen = {}

        cls = Cls(self.__ws, self.__ui_ws, self.__remote_ws)

        for att in dir(cls):
            if not (att.startswith('__') and att.endswith('__')):
                a = cls.__getattribute__(att)
                if callable(a):
                    logger.debug(f"{classname}.{att}: {a}")
                    if isclass(a):
                        gen.update(self.__treegen(a, prefix))
                    else:
                        name = join(prefix, att)
                        validator_kwargs = {'arbitrary_types_allowed': True}
                        try:
                            validator_kwargs.update(a.validator_kwargs)
                        except AttributeError:
                            pass
                        # assign to each function an attribute with its full API path
                        setattr(getattr(Cls, att), 'api_path', name)
                        # transform each function in an async generator
                        gen[name] = self.__make_async_gen(a, validator_kwargs)

        return gen

    @staticmethod
    def __make_async_gen(func, validator_kwargs):
        # noinspection PyArgumentList
        validated_func = validate_call(func, config=validator_kwargs)
        if iscoroutinefunction(func):
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                yield await validated_func(*args, **kwargs)
        elif isgeneratorfunction(func):
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                for ret in validated_func(*args, **kwargs):
                    yield ret
        elif isasyncgenfunction(func):
            async_gen = validated_func
        else:
            @wraps(validated_func)
            async def async_gen(*args, **kwargs):
                yield validated_func(*args, **kwargs)

        async_gen.__original_func__ = func
        async_gen.__validated_func__ = validated_func
        return async_gen

    class Power(WSAPIBase):
        @staticmethod
        @UserPerms.requires.none
        def test_method(ctx: Context, txt='test'):
            """
            Debug method
            """
            logger.debug(txt)
            yield WSResponse(message='Response message test', extra=txt)
            yield WSMulticast(users=ctx.username,  message='Multicast message test', extra=txt)
            yield WSBroadcast(message='Broadcast message test', extra=txt)
            yield {'message': 'Plain dict test', 'extra': txt}

        @staticmethod
        @UserPerms.requires.power
        def reboot():
            cmd_run(['/usr/bin/systemctl', 'reboot', '-i'])

        @staticmethod
        @UserPerms.requires.power
        def poweroff():
            cmd_run(['/usr/bin/systemctl', 'poweroff', '-i'])

logger.debug("WebSocketAPI initialization")
api = WebSocketAPI(WS, UI_WS, REMOTE_WS)
logger.debug("WebSocketAPI initialized")

api.import_class('utils.scheduler', 'Scheduler')
api.import_class('utils.models', 'Settings')
api.import_class('utils.audio', 'Audio', 'Settings')
api.import_class('utils.remote', 'Remote', 'Settings')
api.import_class('utils.network', 'Settings')
api.import_class('utils.system', 'Cron', 'Settings')
api.import_class('utils.security', 'Security', 'Settings')


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    try:
        user:User = await LOGMAN(request)
    except NotAuthenticatedException:
        await websocket.accept()
        await websocket.close(1008, 'Not Authenticated')
        return

    await WS.connect(websocket, user.name)
    await WS.send(websocket, 'connected')
    while True:
        try:
            data: dict[str, Any] = await websocket.receive_json()
            t = data.pop('target')
            try:
                async for ret in handle_call(target=t, user=user, request_data=data):
                    if isinstance(ret, WSBroadcast):
                        await WS.broadcast(ret.target or t, **ret.kwargs)
                    elif isinstance(ret, WSMulticast):
                        await WS.multicast(ret.users, ret.target or t, **ret.kwargs)
                    elif isinstance(ret, WSResponse):
                        await WS.send(websocket, ret.target or t, **ret.kwargs)
                    elif isinstance(ret, dict):
                        await WS.send(websocket, ret.pop('target', t), **ret)
            except KeyError:
                await WS.send(websocket, 'error', error='Invalid command', extra=dumps({'target': t, **data}, indent=4))
            except PermissionError:
                await WS.send(websocket, 'error', error=f'Permission error: not allowed to execute {t}')

        except WebSocketDisconnect:
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            logger.error(format_exc())
    WS.disconnect(websocket)


async def handle_call(target, user, request_data):
    func = api.generators[target]
    # noinspection PyTypeChecker
    check_permissions(func, user)

    #Inspect function signature to check if it has a Context parameter
    signature = inspect.signature(func)

    ctx_param = None
    for param_name, param in signature.parameters.items():
        if param.annotation is Context:
            ctx_param = param_name
            break

    if ctx_param:
        request_data[ctx_param] = Context(username=user.name)

    async for ret in func(**request_data):
        yield ret


def check_permissions(func, user):
    name = func.__name__
    try: name = func.api_path
    except AttributeError: pass

    perms = {UserPerms.admin}
    try:
        if func.perms is None:
            logger.debug(f'{name} requires no permissions to be executed')
            return

        logger.debug(f'{name} requires {' or '.join(func.perms)} permission to be executed')
        perms = func.perms
    except AttributeError:
        logger.debug(f'{name} has no permissions set, assuming admin')

    if user.has_perm.admin or user.perms & perms:
        logger.debug(f'User {user.name} is allowed to execute {name}')
    else:
        logger.critical(f'User {user.name} is not allowed to execute {name}')
        raise PermissionError



@router.websocket("/remote")
async def remote_websocket(websocket: WebSocket):
    if Config.remote_server_ip:
        # immediately refuse connections if remote_server is configured (!=None)
        # this means that this instance is running in client/slave mode
        # and someone is trying either to connect from another client or
        # +----------------------+ is trying to be funny and
        # |                      | discover what happens
        # |    OOOOOOOOO         | when the snake eats itself!
        # |    O  *    O         |
        # |    O  X    O         |
        # |    OOOO    O         |
        # |            O         |
        # |            O         |
        # +----------------------+
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    if not websocket.client or 'instance_id' not in websocket.headers:
        return

    await REMOTE_WS.connect(websocket)

    Config.remote_clients[websocket.headers['instance_id']] = {
        'ip': websocket.client.host,
        'port': websocket.headers.get('port', const.default_port_secure),
        'hostname': websocket.headers.get('hostname', websocket.headers['instance_id'])
    }
    Config.save()

    while True:
        try:
            data = await websocket.receive_text()
            logger.debug(data)
        except WebSocketDisconnect:
            REMOTE_WS.disconnect(websocket)
            break
        except Exception:
            logger.error(format_exc())


DISPLAYS: list[dict] = []
WINDOW = {'bounds': {}, 'orientation': -2, 'flip': -2}


@router.websocket("/ui_ws")
async def ui_websocket(websocket: WebSocket):
    global DISPLAYS, WINDOW
    if websocket.scope['client'][0] != websocket.scope['server'][0]:
        # prohibit external connections
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    await UI_WS.connect(websocket)
    while True:
        try:
            data = await websocket.receive_json()
            match data['target']:
                case 'getAllDisplays':
                    DISPLAYS = data['displays']
                case 'getBounds':
                    WINDOW['bounds'] = data['bounds']
                    await WS.broadcast('Settings/Display/getBounds', **WINDOW['bounds'])
                case 'getOrientation':
                    WINDOW['orientation'] = data['orientation']
                    await WS.broadcast('Settings/Display/getOrientation', orientation=WINDOW['orientation'])
                case 'getFlip':
                    WINDOW['flip'] = data['flip']
                    await WS.broadcast('Settings/Display/getFlip', flip=WINDOW['flip'])
                case 'getAllowInsecureCerts':
                    await WS.broadcast('Settings/Display/allowInsecureCerts', bounds=data['allow'])
                case 'setContainer':
                    try:
                        Config.assets.current.media_type = data['media_type']
                    except IndexError:
                        # no-assets and first-boot pages have an invalid index
                        pass
        except WebSocketDisconnect:
            UI_WS.disconnect(websocket)
            break


class Display(WSAPIBase):
    def getBounds(self):
        """
        Get viewer window bounds
        """
        return WSBroadcast(self.getBounds, **WINDOW['bounds'])

    async def setBounds(self, x: int, y: int, width: int, height: int):
        """
        Set viewer window bounds
        """
        await self.ui_ws.broadcast('setBounds', x=x, y=y, width=width, height=height)

    def getOrientation(self):
        return WSBroadcast(self.getOrientation, orientation=WINDOW['orientation'])

    async def setOrientation(self, orientation: int):
        await self.ui_ws.broadcast('setOrientation', orientation=orientation)

    def getFlip(self):
        return WSBroadcast(self.getFlip, flip=WINDOW['flip'])

    async def setFlip(self, flip: int):
        await self.ui_ws.broadcast('setFlip', flip=flip)


api.load_class(Display, 'Settings')

UPLOADS._callback = lambda x: WS.broadcast('Scheduler/file', files=x)
