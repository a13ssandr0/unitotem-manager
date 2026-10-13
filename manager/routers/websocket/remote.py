from traceback import format_exc

from fastapi import APIRouter
from fastapi import WebSocketException
from loguru import logger
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

from api.ws.endpoints import REMOTE_WS
from routers import pairing
from utils import constants as const
from utils.remote import viewers
from utils.remote.service import service

router = APIRouter()
# The endpoints other nodes call to pair (not websockets, but part of the same feature, and this router is the one
# main.py includes before the scheduler's catch-all)
router.include_router(pairing.router)

_LOCAL_HOSTS = {'127.0.0.1', '::1', 'localhost'}


@router.websocket("/remote")
async def remote_websocket(websocket: WebSocket):
    """The viewer of THIS node. Only from the machine itself: the viewers of other nodes are the clients paired with this
    one and reach it on the mutual-TLS link (utils/remote/link.py), where a certificate nobody paired never gets this far.
    Anything else knocking here from the network is refused - this used to accept any host on the LAN, which could
    register fake viewers and read every command sent to the real ones."""
    if not websocket.client or websocket.client.host not in _LOCAL_HOSTS:
        logger.warning('Refused a viewer connection from {}', websocket.client.host if websocket.client else 'unknown')
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    if 'instance_id' not in websocket.headers:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    instance_id = websocket.headers['instance_id']
    await REMOTE_WS.connect(websocket, user=instance_id)
    await viewers.viewer_connected(instance_id, websocket.client.host,
                                   viewers.parse_port(websocket.headers.get('port'), const.default_port_secure),
                                   websocket.headers.get('hostname', instance_id))

    while True:
        try:
            raw = await websocket.receive_text()
            await viewers.viewer_message(instance_id, raw)
            # a client node: the windows of this machine belong to the server it is paired with, which has to know them
            await service.uplink.forward(raw)
        except WebSocketDisconnect:
            REMOTE_WS.disconnect(websocket, user=instance_id)
            await viewers.viewer_gone(instance_id)
            await service.uplink.viewer_gone()
            break
        except Exception:
            logger.error(format_exc())
