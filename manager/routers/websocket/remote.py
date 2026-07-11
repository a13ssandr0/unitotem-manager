import json
from traceback import format_exc

from fastapi import APIRouter
from fastapi import WebSocketException
from loguru import logger
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

from utils import constants as const
from api.ws.endpoints import REMOTE_WS, WS
from utils.models.remote import RemoteManager

router = APIRouter()

_LOCAL_HOSTS = {'127.0.0.1', '::1', 'localhost'}


@router.websocket("/remote")
async def remote_websocket(websocket: WebSocket):
    remote_manager = RemoteManager.get_instance()

    is_local = websocket.client and websocket.client.host in _LOCAL_HOSTS

    # Refuse remote viewers when this instance is in client mode,
    # but still allow local Qt viewer connections.
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
    if remote_manager.server_ip and not is_local:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    if not websocket.client or 'instance_id' not in websocket.headers:
        return

    instance_id = websocket.headers['instance_id']
    await REMOTE_WS.connect(websocket, user=instance_id)

    # Register viewer with ViewerManager
    viewer_info = {
        'ip': websocket.client.host,
        'port': websocket.headers.get('port', const.default_port_secure),
        'hostname': websocket.headers.get('hostname', instance_id),
        'screens': [],
        'windows': [],
    }
    try:
        from utils.viewer_manager import ViewerManager
        vm = ViewerManager.get_instance()
        vm.register_viewer(instance_id, viewer_info)
        await WS.broadcast('Viewers/list',
                           viewers=vm.get_viewers(),
                           assignments=vm.get_assignments())
    except RuntimeError:
        vm = None

    if not is_local:
        remote_manager.clients[instance_id] = {
            'ip': websocket.client.host,
            'port': websocket.headers.get('port', const.default_port_secure),
            'hostname': websocket.headers.get('hostname', instance_id),
        }
        remote_manager.save()

    while True:
        try:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                target = msg.pop('target', None)
                if target == 'ViewerInfo' and vm:
                    # Viewer sends its screen/window configuration
                    vm.update_viewer_info(instance_id, msg)
                    await WS.broadcast('Viewers/list',
                                       viewers=vm.get_viewers(),
                                       assignments=vm.get_assignments())
            except json.JSONDecodeError:
                logger.debug('Non-JSON message from viewer: {}', raw)
        except WebSocketDisconnect:
            REMOTE_WS.disconnect(websocket, user=instance_id)
            if vm:
                vm.unregister_viewer(instance_id)
                await WS.broadcast('Viewers/list',
                                   viewers=vm.get_viewers(),
                                   assignments=vm.get_assignments())
            if not is_local and instance_id in remote_manager.clients:
                del remote_manager.clients[instance_id]
                remote_manager.save()
            break
        except Exception:
            logger.error(format_exc())
