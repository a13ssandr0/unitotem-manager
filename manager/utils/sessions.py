"""
The websockets of logged-in people, so that a session can be ended from the server.

A session is authenticated once, when its websocket is opened, and stays so: nothing is re-checked while it runs. It ends
only when something happens to the account (its password changes, it is deleted, the users are restored or reset) and then
the server closes the sockets itself, with the reason `Not Authenticated`; the page reloads on that close, which lands on the
login page (utils/models/user.py keeps the matching cookies from being used again).
"""
import asyncio
from typing import Optional

from fastapi import WebSocket
from loguru import logger

NOT_AUTHENTICATED = 'Not Authenticated'

_open: dict[WebSocket, tuple[str, Optional[str]]] = {}     # websocket -> (user name, session id)
_loop: Optional[asyncio.AbstractEventLoop] = None


def track(websocket: WebSocket, user: str, sid: Optional[str]):
    global _loop
    _loop = asyncio.get_running_loop()
    _open[websocket] = (user, sid)


def untrack(websocket: WebSocket):
    _open.pop(websocket, None)


async def close(user: Optional[str] = None, keep_sid: Optional[str] = None):
    """Close the sockets of one user (every user when none is given), except those of the session `keep_sid`."""
    for websocket, (name, sid) in list(_open.items()):
        if (user is None or name == user) and (keep_sid is None or sid != keep_sid):
            _open.pop(websocket, None)
            try:
                await websocket.close(1008, NOT_AUTHENTICATED)
            except RuntimeError:
                pass            # already closing
            except Exception as e:
                logger.warning('Closing a websocket of {} failed: {}', name, e)
            else:
                logger.info('Session of {} ended by the server', name)


def close_soon(user: Optional[str] = None, keep_sid: Optional[str] = None):
    """Same, from code that is not async (and maybe not on the event loop's thread)."""
    if _loop is None or _loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(close(user, keep_sid), _loop)
