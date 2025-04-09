from json import dumps
from traceback import format_exc
from typing import Any

from fastapi import APIRouter
from loguru import logger
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from utils.models.user import User
from api.ws.endpoints import WS, handle_call
from api.ws.responses import WSBroadcast, WSMulticast, WSResponse
from routers.login import LOGMAN, NotAuthenticatedException

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    try:
        user: User = await LOGMAN(request)
    except NotAuthenticatedException:
        await websocket.accept()
        await websocket.close(1008, 'Not Authenticated')
        logger.warning('An user tried connect without being logged in')
        return

    await WS.connect(websocket, user.name)
    await WS.send(websocket, 'connected')
    logger.success('User {} connected', user.name)

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
                logger.error('Invalid command: {}', {'target': t, **data})
            except PermissionError:
                await WS.send(websocket, 'error', error=f'Permission error: not allowed to execute {t}')
                logger.error('Permission error: not allowed to execute {}', t)

        except WebSocketDisconnect:
            logger.success('User {} disconnected', user.name)
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            logger.error(format_exc())
    WS.disconnect(websocket)
