from inspect import isclass, isgeneratorfunction, iscoroutinefunction
from json import dumps
from os.path import join
from subprocess import run as cmd_run
from traceback import print_exc, format_exc
from typing import Any

from fastapi import APIRouter, WebSocketException, Request, status, WebSocket, WebSocketDisconnect

import utils.constants as const
from utils.models import Config
from utils.security import LOGMAN, NotAuthenticatedException
from .wsmanager import WSManager, WSAPIBase

router = APIRouter()
REMOTE_WS = WSManager(True)
UI_WS = WSManager(True)
WS = WSManager()


# noinspection PyUnresolvedReferences
class WebSocketAPI:
    callables = {}
    generators = {}
    awaitables = {}

    def __init__(self, ws: WSManager, ui_ws: WSManager, remote_ws: WSManager):
        self.__ws = ws
        self.__ui_ws = ui_ws
        self.__remote_ws = remote_ws
        self.load_class(self.Power)

    def load_class(self, cls: type, prefix: str = None):
        if not issubclass(cls, WSAPIBase):
            raise ValueError("Class is not a subclass of WSAPIBase")

        cal, gen, awa = self.__treegen(cls, prefix)
        self.callables.update(cal)
        self.generators.update(gen)
        self.awaitables.update(awa)

    def __treegen(self, Cls: type, prefix: str = None):
        classname = Cls.__name__
        print("Class:", classname)
        if prefix is None:
            prefix = classname
        else:
            prefix = join(prefix, classname)

        cal = {}
        gen = {}
        awa = {}

        cls = Cls(self.__ws, self.__ui_ws, self.__remote_ws)

        for att in dir(cls):
            if not att.startswith('__'):
                a = cls.__getattribute__(att)
                if callable(a):
                    if isclass(a):
                        c, g, a = self.__treegen(a, prefix)
                        cal.update(c)
                        gen.update(g)
                        awa.update(a)
                    elif isgeneratorfunction(a):
                        print("Generator:", att)
                        gen[join(prefix, att).lower()] = a
                    elif iscoroutinefunction(a):
                        print("Awaitable:", att)
                        awa[join(prefix, att).lower()] = a
                    else:
                        print("Callable:", att)
                        cal[join(prefix, att).lower()] = a

        return cal, gen, awa

    class Power(WSAPIBase):
        @staticmethod
        def test_method(txt='test'):
            print(txt)
            return txt

        @staticmethod
        def reboot():
            cmd_run(['/usr/bin/systemctl', 'reboot', '-i'])

        @staticmethod
        def poweroff():
            cmd_run(['/usr/bin/systemctl', 'poweroff', '-i'])


api = WebSocketAPI(WS, UI_WS, REMOTE_WS)
from utils.scheduler import Scheduler
api.load_class(Scheduler)
from utils.audio import Audio
api.load_class(Audio, 'settings')
from utils.network import Settings
api.load_class(Settings)
from utils.system import Cron
api.load_class(Cron, 'settings')
print()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    try:
        await LOGMAN(request)
    except NotAuthenticatedException:
        await websocket.accept()
        await websocket.close(1008, 'Not Authenticated')
        return

    await WS.connect(websocket)
    await WS.send(websocket, 'connected')
    while True:
        # noinspection PyBroadException
        try:
            data: dict[str, Any] = await websocket.receive_json()
            t = data.pop('target')
            try:
                await WS.handlers[t](websocket, **data)
            except KeyError:
                await WS.send(websocket, 'error', error='Invalid command', extra=dumps({'target': t, **data}, indent=4))

        except WebSocketDisconnect:
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            print_exc()
    WS.disconnect(websocket)


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
        # noinspection PyBroadException
        try:
            data = await websocket.receive_text()
            print(data)
        except WebSocketDisconnect:
            REMOTE_WS.disconnect(websocket)
            break
        except Exception:
            print_exc()


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
                    await WS.broadcast('settings/display/getBounds', **WINDOW['bounds'])
                case 'getOrientation':
                    WINDOW['orientation'] = data['orientation']
                    await WS.broadcast('settings/display/getOrientation', orientation=WINDOW['orientation'])
                case 'getFlip':
                    WINDOW['flip'] = data['flip']
                    await WS.broadcast('settings/display/getFlip', flip=WINDOW['flip'])
                case 'getAllowInsecureCerts':
                    await WS.broadcast('settings/display/allowInsecureCerts', bounds=data['allow'])
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


api.load_class(Display, 'settings')
