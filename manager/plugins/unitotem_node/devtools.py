"""
The developer tools of a window's page, in the browser of an administrator instead of on the totem's screen.

The viewer's DevTools endpoint listens on the loopback only and is never exposed. This module is the one way to it, with the
same locks as the terminal and the screen: administrator only, the session cookie, Origin = this host, and a one-time token
(valid 30 s, spent by being looked at, bound to the account, the session and the page it was asked for).

 - `POST /devtools/open {window_id}` finds the page of that window and answers the address of the front end with a token in it;
 - `GET /devtools/<file>` serves the front end's own files (HTML, scripts, styles), nothing else of the endpoint;
 - `WS /devtools/ws/<token>/<page>` is the protocol, bridged to the page's websocket. The front end opens it by itself, so the
   token cannot be a first frame as for the terminal: it is in the address, which is why it is one-time and bound to the page.
"""
import asyncio
import re

import aiohttp
import websockets
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from routers.login import require_perm
from utils import sessions
from .vnc import authorize
from .ws_token import new_token, take_token

router = APIRouter()

ENDPOINT = '127.0.0.1:9223'          # app.py CDP_PORT of the viewer
MAX_SESSIONS = 4
MAX_FILE = 32 * 1024 * 1024
_sessions = 0
_PAGE_ID = re.compile(r'[0-9A-Fa-f]{32}')
_FILE = re.compile(r'(?!.*\.\.)[A-Za-z0-9_./-]{1,200}')
# the endpoint's protocol and its target list (`json` lists, creates and closes targets): never served as files
_NOT_FILES = ('page', 'browser', 'ws', 'json')
_WINDOW = re.compile(r'[?&]window=(\d+)(?:&|$)')


class OpenRequest(BaseModel):
    window_id: int


async def _targets() -> list[dict]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
        async with client.get(f'http://{ENDPOINT}/json') as reply:
            return await reply.json(content_type=None)


@router.post('/devtools/open')
async def devtools_open(request: OpenRequest, user=Depends(require_perm('admin'))):
    """The address that opens the developer tools of the page shown in a window."""
    try:
        targets = await _targets()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        raise HTTPException(503, 'The viewer is not running')
    for target in targets:
        match = _WINDOW.search(target.get('url', ''))
        if target.get('type') == 'page' and match and int(match.group(1)) == request.window_id \
                and _PAGE_ID.fullmatch(target.get('id', '')):
            page = target['id']
            token = new_token(user.name, user.sid, scope=page)
            logger.warning('{} opens the developer tools of window {}', user.name, request.window_id)
            return {'path': f'/devtools/inspector.html?wss={{host}}/devtools/ws/{token}/{page}'}
    raise HTTPException(404, 'That window has no page')


@router.get('/devtools/{path:path}')
async def devtools_file(path: str, user=Depends(require_perm('admin'))):
    """A file of the front end. The protocol endpoints (`page/`, `browser/`) and the target list are not files."""
    if not _FILE.fullmatch(path) or path.split('/')[0] in _NOT_FILES:
        raise HTTPException(404)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
            async with client.get(f'http://{ENDPOINT}/devtools/{path}') as reply:
                if reply.status != 200:
                    raise HTTPException(reply.status if reply.status in (404, 403) else 502)
                body = bytearray()
                async for chunk in reply.content.iter_chunked(65536):       # read() would return only what has arrived
                    body += chunk
                    if len(body) > MAX_FILE:
                        raise HTTPException(502, 'File too large')
                body = bytes(body)
                kind = reply.headers.get('Content-Type', 'application/octet-stream')
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise HTTPException(503, 'The viewer is not running')
    return Response(body, media_type=kind.split(';')[0], headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY'})


async def _pump(source, sink):
    """Frames from one side to the other until either ends."""
    async for message in source:
        await sink(message)


@router.websocket('/devtools/ws/{token}/{page}')
async def devtools_socket(websocket: WebSocket, token: str, page: str):
    global _sessions
    user = await authorize(websocket)
    if user is None:
        return
    peer = websocket.client.host if websocket.client else 'unknown'
    await websocket.accept()
    if not _PAGE_ID.fullmatch(page) or not take_token(token, user.name, user.sid, scope=page):
        logger.warning('Developer tools refused to {} from {}: missing, wrong, used or expired token', user.name, peer)
        await websocket.close(1008, 'Forbidden')
        return
    if _sessions >= MAX_SESSIONS:
        await websocket.close(1013, 'Too many developer tools are open')
        return
    _sessions += 1
    try:
        async with websockets.connect(f'ws://{ENDPOINT}/devtools/page/{page}', origin=None, max_size=None,
                                      ping_interval=None) as page_socket:
            sessions.track(websocket, user.name, user.sid)

            async def browser_frames():
                try:
                    while True:
                        yield await websocket.receive_text()
                except (WebSocketDisconnect, RuntimeError):
                    return

            tasks = [asyncio.ensure_future(_pump(page_socket, websocket.send_text)),
                     asyncio.ensure_future(_pump(browser_frames(), page_socket.send))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                task.cancel()
    except (OSError, websockets.WebSocketException) as exc:
        logger.warning('Developer tools: the page is not reachable: {}', exc)
    finally:
        _sessions -= 1
        sessions.untrack(websocket)
        try:
            await websocket.close()
        except RuntimeError:
            pass
