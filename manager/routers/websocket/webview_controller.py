from fastapi import APIRouter, WebSocketException
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

import api.display
from api.models import Config
from api.ws.endpoints import UI_WS, WS

router = APIRouter()


@router.websocket("/ui_ws")
async def ui_websocket(websocket: WebSocket):
    if websocket.scope['client'][0] != websocket.scope['server'][0]:
        # prohibit external connections
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    await UI_WS.connect(websocket)
    while True:
        try:
            data = await websocket.receive_json()
            match data['target']:
                case 'getAllDisplays':
                    api.display.DISPLAYS = data['displays']
                case 'getBounds':
                    api.display.WINDOW['bounds'] = data['bounds']
                    await WS.broadcast('Settings/Display/getBounds', **api.display.WINDOW['bounds'])
                case 'getOrientation':
                    api.display.WINDOW['orientation'] = data['orientation']
                    await WS.broadcast('Settings/Display/getOrientation', orientation=api.display.WINDOW['orientation'])
                case 'getFlip':
                    api.display.WINDOW['flip'] = data['flip']
                    await WS.broadcast('Settings/Display/getFlip', flip=api.display.WINDOW['flip'])
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
