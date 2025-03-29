from traceback import format_exc

from fastapi import APIRouter
from fastapi import WebSocketException
from loguru import logger
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

from api import constants as const
from api.models import Config
from api.ws.endpoints import REMOTE_WS

router = APIRouter()


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
