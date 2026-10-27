"""
A terminal in the web UI: `/terminal` is a websocket carrying a pty that runs the shell of the web user's own account.

There is no second login, the way TrueNAS does it (read in its sources: `apps/webshell_app.py` and `plugins/auth.py` of
truenas/middleware, `services/shell.service.ts` of truenas/webui): the web user is already authenticated, the web
accounts are operating-system accounts (PAM), so the console starts `login -f <that account>` and the visitor is in their own shell, as
themselves, with no user name or password to type. What keeps this safe:
 - opening it needs the admin capability, checked when the token is asked for AND when the websocket is opened;
 - the websocket is opened with a ONE-TIME TOKEN (`POST /terminal/token`, valid 30 s, tied to the account and to the
   session that asked for it), sent as the first text frame: a page of another site cannot get one (the session cookie is
   SameSite=Strict and the token needs it), and a token seen in a log or a history is worthless once used;
 - the Origin of the websocket must be this host;
 - an account with no shell (`nologin`, `false`: the accounts the web UI creates) gets a message, not a shell;
 - opening and closing are logged with the account and the address.

The session ends when the shell does (logout, Ctrl+D), when the window is closed, at the server's idle limit, and when the
web session ends (utils/sessions.py closes the socket).

Protocol: the first frame is text, `{"token": "..."}`; then binary frames are terminal data in both directions and a text
frame is JSON, `{"resize": [cols, rows]}`.
"""
import asyncio
import fcntl
import json
import os
import pty
import pwd
import signal
import struct
import termios
import time

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from .ws_token import new_token, read_token, same_origin
from routers.login import LOGMAN, NotAuthenticatedException, require_perm
from utils import sessions

router = APIRouter()

MAX_SESSIONS = 4
IDLE_TIMEOUT = 15 * 60          # seconds without a keystroke
LOGIN_PROGRAM = '/bin/login'
NO_SHELL = ('nologin', 'false')
_sessions: set[int] = set()


def _set_size(fd: int, cols: int, rows: int):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', max(rows, 1), max(cols, 1), 0, 0))


def has_shell(account: str) -> bool:
    try:
        shell = pwd.getpwnam(account).pw_shell
    except KeyError:
        return False
    return bool(shell) and os.path.basename(shell) not in NO_SHELL


async def _terminate(proc: asyncio.subprocess.Process):
    """Hang up the session and make sure the child is gone and reaped."""
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
        if proc.returncode is not None:
            return
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), 1.5)
            return
        except asyncio.TimeoutError:
            continue


class Terminal:
    """One open terminal: a pty, `login -f` on it, and the pumps between it and the websocket."""

    def __init__(self, websocket: WebSocket, username: str):
        self.websocket = websocket
        self.username = username
        self.loop = asyncio.get_running_loop()
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.master, self.slave = pty.openpty()
        os.set_blocking(self.master, False)
        self.proc: asyncio.subprocess.Process | None = None
        self.last_input = time.monotonic()

    async def start(self):
        self.loop.add_reader(self.master, self._on_readable)
        # Not os.fork()/pty.fork(): this process is multi-threaded (Qt, CEF) and forking it is unsafe. The child is
        # started by the subprocess machinery with the pty as its stdio, and `setsid -c` makes that pty its
        # controlling terminal, which login needs. `-f`: the account is already authenticated (the web session).
        self.proc = await asyncio.create_subprocess_exec(
            'setsid', '-c', LOGIN_PROGRAM, '-f', self.username, stdin=self.slave, stdout=self.slave, stderr=self.slave,
            close_fds=True, env={'TERM': 'xterm-256color', 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'})
        _sessions.add(self.proc.pid)

    def _on_readable(self):
        try:
            data = os.read(self.master, 65536)
        except (BlockingIOError, OSError):
            return        # woken with nothing to read; the end of the shell is seen by `supervise`
        if data:
            self.output.put_nowait(data)

    async def pump_output(self):
        while (chunk := await self.output.get()) is not None:
            await self.websocket.send_bytes(chunk)
        await self.websocket.close(1000, 'Session ended')

    async def watch_idle(self):
        while True:
            await asyncio.sleep(30)
            if time.monotonic() - self.last_input > IDLE_TIMEOUT:
                logger.info('Terminal of {} closed after {} s without input', self.username, IDLE_TIMEOUT)
                await self.websocket.close(1000, 'Idle')
                return

    async def supervise(self):
        """When the shell ends the window ends with it (after the reader has delivered what it printed last)."""
        await self.proc.wait()
        await asyncio.sleep(0.15)
        self.output.put_nowait(None)

    def write(self, data: bytes):
        self.last_input = time.monotonic()
        os.write(self.master, data)

    def resize(self, cols: int, rows: int):
        _set_size(self.master, cols, rows)

    async def close(self):
        self.loop.remove_reader(self.master)
        if self.proc is not None:
            await _terminate(self.proc)
            _sessions.discard(self.proc.pid)
            if self.proc.returncode not in (0, 1, -signal.SIGHUP, -signal.SIGTERM, -signal.SIGKILL):
                logger.warning('login for {} ended with status {}', self.username, self.proc.returncode)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@router.post('/terminal/token')
async def terminal_token(user=Depends(require_perm('admin'))):
    """A one-time token to open the terminal websocket with (valid 30 s, for this session only)."""
    if not has_shell(user.name):
        raise HTTPException(status_code=409, detail='This account has no shell (its login shell is nologin)')
    return {'token': new_token(user.name, user.sid)}


async def _refuse(websocket: WebSocket, code: int, reason: str):
    await websocket.accept()
    await websocket.close(code, reason)


@router.websocket('/terminal')
async def terminal(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    peer = websocket.client.host if websocket.client else 'unknown'
    try:
        user = await LOGMAN(request)
    except NotAuthenticatedException:
        return await _refuse(websocket, 1008, 'Not Authenticated')
    if not same_origin(websocket):
        logger.warning('Terminal refused to {} from {}: foreign origin {}', user.name, peer, websocket.headers.get('origin'))
        return await _refuse(websocket, 1008, 'Forbidden')
    if not user.has_perm.admin:
        logger.warning('Terminal refused to {} from {}: not an admin', user.name, peer)
        return await _refuse(websocket, 1008, 'Forbidden')
    if len(_sessions) >= MAX_SESSIONS:
        return await _refuse(websocket, 1013, 'Too many open terminals')

    await websocket.accept()
    # The first frame carries the one-time token; nothing is started before it is right
    if not await read_token(websocket, user.name, user.sid):
        logger.warning('Terminal refused to {} from {}: missing, wrong, used or expired token', user.name, peer)
        await websocket.close(1008, 'Forbidden')
        return
    if not has_shell(user.name):
        await websocket.close(1008, 'This account has no shell')
        return

    sessions.track(websocket, user.name, user.sid)
    term = Terminal(websocket, user.name)
    try:
        await term.start()
    except OSError as exc:
        logger.error('Cannot start {}: {}', LOGIN_PROGRAM, exc)
        await term.close()
        await websocket.close(1011, 'Cannot start the terminal')
        sessions.untrack(websocket)
        return

    started = time.monotonic()
    logger.info('Terminal opened by web user {} from {} (pid {})', user.name, peer, term.proc.pid)
    tasks = [asyncio.create_task(term.pump_output()), asyncio.create_task(term.watch_idle()),
             asyncio.create_task(term.supervise())]
    try:
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                break
            if message.get('bytes') is not None:
                try:
                    term.write(message['bytes'])
                except OSError:
                    pass
            elif message.get('text') is not None:
                try:
                    cols, rows = json.loads(message['text'])['resize']
                    term.resize(int(cols), int(rows))
                except (ValueError, KeyError, TypeError, OSError):
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        logger.exception('Terminal of {} failed', user.name)
    finally:
        sessions.untrack(websocket)
        for task in tasks:
            task.cancel()
        await term.close()
        logger.info('Terminal of {} closed after {:.0f} s', user.name, time.monotonic() - started)
