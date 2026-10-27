"""
One-time tokens for the websockets that give a window onto the machine (the terminal and the screen).

The page asks for a
token over an ordinary authenticated request, and the websocket must present it as its first text frame, `{"token": "..."}`.
A token is valid for TOKEN_TTL seconds, only once (it is spent by being looked at, right or wrong), and only for the
account and the session that asked for it: the session cookie alone is not enough to open these sockets, and a token left in a
log or a history is worthless. The Origin of the websocket must be this host as well.
"""
import asyncio
import json
import secrets
import time
from urllib.parse import urlsplit

from starlette.websockets import WebSocket, WebSocketDisconnect

TOKEN_TTL = 30
_tokens: dict[str, tuple[str, str | None, float]] = {}      # token -> (account, session id, expiry on the monotonic clock)


def same_origin(websocket: WebSocket) -> bool:
    """A browser always sends Origin on a websocket: a page of another site must not be able to open this one."""
    origin = websocket.headers.get('origin')
    return origin is None or urlsplit(origin).netloc == websocket.headers.get('host')


def new_token(user: str, sid: str | None) -> str:
    now = time.monotonic()
    for old in [t for t, (_, _, expiry) in _tokens.items() if expiry < now]:
        del _tokens[old]
    token = secrets.token_urlsafe(32)
    _tokens[token] = (user, sid, now + TOKEN_TTL)
    return token


def take_token(token: str, user: str, sid: str | None) -> bool:
    """Spends a token: true only if it exists, has not expired and was asked for by this account in this session."""
    entry = _tokens.pop(token, None)
    if entry is None:
        return False
    account, session, expiry = entry
    return expiry >= time.monotonic() and account == user and session == sid


async def read_token(websocket: WebSocket, user: str, sid: str | None) -> bool:
    """Reads the first frame of an accepted websocket and spends the token in it."""
    try:
        token = json.loads(await asyncio.wait_for(websocket.receive_text(), TOKEN_TTL)).get('token')
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, ValueError, AttributeError):
        return False
    return isinstance(token, str) and take_token(token, user, sid)
