"""
A terminal in the web UI: `/terminal` is a websocket carrying a pty that runs `login`.

It is `login`, not a shell, on purpose: the manager runs as root, so a shell started directly would make anybody who
can open it root. `login` asks for an operating-system account and password (PAM, with its own failure delay and
logging), separate from the web UI's users, and the session ends up as that account. Opening it needs the admin
capability on top of that.

The connection is only ended by the user: by logging out of the session they had, or by closing the window. `login`
itself gives up when nobody logs in (after 60 s at the prompt, and after three wrong passwords): that is not a decision
of the user, so it is started again on a new terminal and the prompt comes back. The only other endings are the
server's own idle limit and the connection being lost, which the window shows without closing.

Protocol: binary frames are terminal data in both directions; a text frame is JSON, `{"resize": [cols, rows]}`.
"""
import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import termios
import time

from fastapi import APIRouter
from loguru import logger
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from routers.login import LOGMAN, NotAuthenticatedException

router = APIRouter()

MAX_SESSIONS = 4
IDLE_TIMEOUT = 15 * 60          # seconds without a keystroke
LOGIN_PROGRAM = '/bin/login'
RESTART_MIN_INTERVAL = 2.0      # a login that dies sooner than this is not a timeout but a failure to start
RESTART_MAX_QUICK = 5
_sessions: set[int] = set()


def _set_size(fd: int, cols: int, rows: int):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', max(rows, 1), max(cols, 1), 0, 0))


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


def _has_session(proc: asyncio.subprocess.Process) -> bool:
    """login forks the user's shell and waits for it: while it has a child, somebody is logged in."""
    try:
        with open(f'/proc/{proc.pid}/task/{proc.pid}/children') as file:
            return bool(file.read().strip())
    except OSError:
        return False


class Terminal:
    """One open terminal: a pty, the login process on it, and the pumps between it and the websocket."""

    def __init__(self, websocket: WebSocket, username: str):
        self.websocket = websocket
        self.username = username
        self.loop = asyncio.get_running_loop()
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.master = -1
        self.slave = -1
        self.proc: asyncio.subprocess.Process | None = None
        self.size: tuple[int, int] | None = None
        self.logged_in = False
        self.closing = False
        self.last_input = time.monotonic()

    # A pty cannot be reused once login has ended on it: login hangs the terminal up, and a login started on it
    # afterwards reads end-of-file and exits at once. Every login therefore gets a pty of its own.
    async def start_login(self):
        if self.master >= 0:
            self.loop.remove_reader(self.master)
            os.close(self.master)
            os.close(self.slave)
        self.master, self.slave = pty.openpty()
        os.set_blocking(self.master, False)
        if self.size:
            _set_size(self.master, *self.size)
        self.loop.add_reader(self.master, self._on_readable, self.master)
        self.logged_in = False
        # Not os.fork()/pty.fork(): this process is multi-threaded (Qt, CEF) and forking it is unsafe. The child is
        # started by the subprocess machinery with the pty as its stdio, and `setsid -c` makes that pty its
        # controlling terminal, which login needs.
        self.proc = await asyncio.create_subprocess_exec(
            'setsid', '-c', LOGIN_PROGRAM, stdin=self.slave, stdout=self.slave, stderr=self.slave, close_fds=True,
            env={'TERM': 'xterm-256color', 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'})
        _sessions.add(self.proc.pid)

    def _on_readable(self, fd: int):
        if fd != self.master:
            return
        try:
            data = os.read(fd, 65536)
        except (BlockingIOError, OSError):
            return        # woken with nothing to read; the end of a login is detected by `supervise`
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
        """login ends for two different reasons. The user logged out of a session they had: the window closes. Or
        login gave up without anybody having logged in: nothing the user asked for has happened, so login is started
        again and the window stays."""
        quick = 0
        try:
            while True:
                proc = self.proc
                started = time.monotonic()
                waiter = asyncio.ensure_future(proc.wait())
                while not waiter.done():
                    if _has_session(proc):
                        self.logged_in = True
                    await asyncio.wait({waiter}, timeout=0.2)
                await asyncio.sleep(0.15)     # let the reader deliver what login printed last
                _sessions.discard(proc.pid)
                if self.closing or self.logged_in:
                    break
                if time.monotonic() - started < RESTART_MIN_INTERVAL:
                    quick += 1
                    if quick >= RESTART_MAX_QUICK:
                        logger.error('login keeps failing to start for {}, giving up', self.username)
                        break
                    await asyncio.sleep(1)
                else:
                    quick = 0
                logger.info('login for {} ended before anybody logged in (status {}): starting it again',
                            self.username, proc.returncode)
                await self.start_login()
        except Exception:
            logger.exception('Terminal supervisor of {} failed', self.username)
        self.output.put_nowait(None)

    def write(self, data: bytes):
        self.last_input = time.monotonic()
        os.write(self.master, data)

    def resize(self, cols: int, rows: int):
        self.size = (cols, rows)
        _set_size(self.master, cols, rows)

    async def close(self):
        self.closing = True
        if self.master >= 0:
            self.loop.remove_reader(self.master)
        if self.proc is not None:
            await _terminate(self.proc)
            _sessions.discard(self.proc.pid)
            if self.proc.returncode not in (0, 1, -signal.SIGHUP, -signal.SIGTERM, -signal.SIGKILL):
                logger.warning('login for {} ended with status {}', self.username, self.proc.returncode)
        for fd in (self.master, self.slave):
            if fd >= 0:
                os.close(fd)


@router.websocket('/terminal')
async def terminal(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    peer = websocket.client.host if websocket.client else 'unknown'
    try:
        user = await LOGMAN(request)
    except NotAuthenticatedException:
        await websocket.accept()
        await websocket.close(1008, 'Not Authenticated')
        return
    if not user.has_perm.admin:
        logger.warning('Terminal refused to {} from {}: not an admin', user.name, peer)
        await websocket.accept()
        await websocket.close(1008, 'Forbidden')
        return
    if len(_sessions) >= MAX_SESSIONS:
        await websocket.accept()
        await websocket.close(1013, 'Too many open terminals')
        return

    await websocket.accept()
    term = Terminal(websocket, user.name)
    try:
        await term.start_login()
    except OSError as exc:
        logger.error('Cannot start {}: {}', LOGIN_PROGRAM, exc)
        await term.close()
        await websocket.close(1011, 'Cannot start the terminal')
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
                    pass      # between two logins; the keystroke is lost, the prompt is coming back
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
        term.closing = True
        for task in tasks:
            task.cancel()
        await term.close()
        logger.info('Terminal of {} closed after {:.0f} s', user.name, time.monotonic() - started)
