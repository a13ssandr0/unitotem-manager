"""
The screen of the totem in the web UI: `/vnc` is a websocket carrying the RFB stream of a VNC server started for the
occasion (see system/vnc.py for the server and why it is reachable through nothing else).

Only an administrator gets in, with the same session cookie as the rest of the interface; everybody else is turned away
before anything is started. Query parameters: `output` (a connected output, absent for the compositor's first) and `control=1`
to let the administrator type and click. The default is view-only, and that is enforced by the server.

The session ends when the browser closes the websocket, when the browser has sent no sign of life of the user for
IDLE_TIMEOUT, or when the server dies; in every case the server is stopped and its directory removed. The window in the
browser never closes by itself: it shows why the connection ended.

Browser -> server: binary frames are RFB; a text frame `{"activity": 1}` says that the user is still there (noVNC
itself talks constantly, so the traffic cannot tell an attentive administrator from an abandoned tab).

The bridge is a few lines of asyncio streams rather than python3-websockify: Starlette already terminates the
websocket, with the session and the permission check of every other route, and websockify would bring its own server
loop, token plugins and (on Debian) numpy, jwcrypto and redis in order to copy bytes between two sockets.
"""
import asyncio
import json
from urllib.parse import urlsplit

from fastapi import APIRouter
from loguru import logger
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from routers.login import LOGMAN, NotAuthenticatedException
from utils import sessions
from .system import vnc as vnc_system
from .system.screens import wayland
from .system.vnc import IdleWatch, VncError, VncServer

router = APIRouter()
_sessions: set[VncServer] = set()
IDLE_CHECK_INTERVAL = 5


async def _refuse(websocket: WebSocket, code: int, reason: str):
    await websocket.accept()
    await websocket.close(code, reason)


def same_origin(websocket: WebSocket) -> bool:
    """A browser always sends Origin on a websocket: a page of another site must not be able to open this one even
    if it somehow rode the user's cookie (it is SameSite=Strict, this is the second lock)."""
    origin = websocket.headers.get('origin')
    return origin is None or urlsplit(origin).netloc == websocket.headers.get('host')


async def authorize(websocket: WebSocket):
    """The administrator behind the websocket, or None after refusing it."""
    peer = websocket.client.host if websocket.client else 'unknown'
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    try:
        user = await LOGMAN(request)
    except NotAuthenticatedException:
        await _refuse(websocket, 1008, 'Not Authenticated')
        return None
    if not user.has_perm.admin:
        logger.warning('Screen viewing refused to {} from {}: not an admin', user.name, peer)
        await _refuse(websocket, 1008, 'Forbidden')
        return None
    if not same_origin(websocket):
        logger.warning('Screen viewing refused to {} from {}: foreign origin {}', user.name, peer,
                       websocket.headers.get('origin'))
        await _refuse(websocket, 1008, 'Forbidden')
        return None
    return user


async def _pick_output(name: str | None):
    """The Output to show, None for the compositor's first. Raises VncError for an output that does not exist."""
    if not name:
        return None
    for output in await wayland.outputs():
        if output.name == name:
            return output
    raise VncError(f'Unknown output {name}')


async def _to_server(websocket: WebSocket, writer: asyncio.StreamWriter, idle: IdleWatch):
    while True:
        message = await websocket.receive()
        if message['type'] == 'websocket.disconnect':
            return 'The browser went away'
        if message.get('bytes') is not None:
            writer.write(message['bytes'])
            await writer.drain()
        elif message.get('text') is not None:
            try:
                if json.loads(message['text']).get('activity'):
                    idle.touch()
            except (ValueError, AttributeError):
                pass


async def _to_browser(websocket: WebSocket, reader: asyncio.StreamReader):
    while data := await reader.read(65536):
        await websocket.send_bytes(data)
    return 'The VNC server ended'


async def _watch_idle(idle: IdleWatch):
    while not idle.expired():
        await asyncio.sleep(min(IDLE_CHECK_INTERVAL, max(idle.remaining(), 0.01)))
    return 'Idle'


@router.websocket('/vnc')
async def vnc(websocket: WebSocket):
    user = await authorize(websocket)
    if user is None:
        return
    peer = websocket.client.host if websocket.client else 'unknown'
    control = websocket.query_params.get('control') == '1'

    binary = vnc_system.find_binary()
    if binary is None:
        await _refuse(websocket, 1011, 'The VNC server is not installed (package wayvnc)')
        return
    if len(_sessions) >= vnc_system.MAX_SESSIONS:
        await _refuse(websocket, 1013, 'Too many screens are being watched')
        return

    server = None
    try:
        output = await _pick_output(websocket.query_params.get('output'))
        server = VncServer(binary=binary, control=control, output=vnc_system.output_name(output))
        _sessions.add(server)
        await server.start()
        reader, writer = await server.connect()
    except VncError as exc:
        if server:
            _sessions.discard(server)
            await server.stop()
        await _refuse(websocket, 1011, str(exc)[:120])
        return

    await websocket.accept()
    sessions.track(websocket, user.name, user.sid)
    logger.info('{} watches the screen ({}) from {}, {}', user.name, output.name if output else 'first output', peer,
                'WITH control' if control else 'view-only')
    idle = IdleWatch()
    tasks = [asyncio.create_task(_to_server(websocket, writer, idle)),
             asyncio.create_task(_to_browser(websocket, reader)),
             asyncio.create_task(_server_exit(server))]
    if idle.timeout > 0:
        tasks.append(asyncio.create_task(_watch_idle(idle)))
    reason = 'The connection was lost'
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is None:
                reason = task.result()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        sessions.untrack(websocket)
        writer.close()
        _sessions.discard(server)
        await server.stop()
        logger.info('Screen viewing of {} ended: {}', user.name, reason)
        try:
            await websocket.close(1000, reason)
        except Exception:
            pass      # the browser is already gone


async def _server_exit(server: VncServer):
    await server.proc.wait()
    return 'The VNC server ended'


async def stop_all():
    """On shutdown: no capture of the screen outlives the manager."""
    for server in list(_sessions):
        await server.stop()
    _sessions.clear()
