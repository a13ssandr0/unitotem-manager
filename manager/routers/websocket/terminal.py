"""
A terminal in the web UI: `/terminal` is a websocket carrying a pty that runs `login`.

It is `login`, not a shell, on purpose: the manager runs as root, so a shell started directly would make anybody who
can open it root. `login` asks for an operating-system account and password (PAM, with its own failure delay and
logging), separate from the web UI's users, and the session ends up as that account. Opening it needs the admin
capability on top of that.

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
    # Not os.fork()/pty.fork(): this process is multi-threaded (Qt, CEF) and forking it is unsafe. The child is
    # started by the subprocess machinery with the pty as its stdio, and `setsid -c` makes that pty its controlling
    # terminal, which login needs.
    fd, slave = pty.openpty()
    try:
        proc = await asyncio.create_subprocess_exec(
            'setsid', '-c', LOGIN_PROGRAM, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
            env={'TERM': 'xterm-256color', 'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'})
    except OSError as exc:
        logger.error('Cannot start {}: {}', LOGIN_PROGRAM, exc)
        os.close(fd)
        os.close(slave)
        await websocket.close(1011, 'Cannot start the terminal')
        return
    os.close(slave)
    pid = proc.pid
    _sessions.add(pid)
    started = time.monotonic()
    logger.info('Terminal opened by web user {} from {} (pid {})', user.name, peer, pid)
    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes | None] = asyncio.Queue()
    os.set_blocking(fd, False)

    def on_readable():
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return            # woken with nothing to read: that is not the end of the session
        except OSError:       # EIO: the child closed its side, the session is over
            data = b''
        output.put_nowait(data or None)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, on_readable)
    last_input = time.monotonic()

    async def pump_output():
        while (chunk := await output.get()) is not None:
            await websocket.send_bytes(chunk)
        await websocket.close(1000, 'Session ended')

    async def watch_idle():
        while True:
            await asyncio.sleep(30)
            if time.monotonic() - last_input > IDLE_TIMEOUT:
                logger.info('Terminal of {} closed after {} s without input', user.name, IDLE_TIMEOUT)
                await websocket.close(1000, 'Idle')
                return

    tasks = [asyncio.create_task(pump_output()), asyncio.create_task(watch_idle())]
    try:
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                break
            if message.get('bytes') is not None:
                last_input = time.monotonic()
                os.write(fd, message['bytes'])
            elif message.get('text') is not None:
                try:
                    cols, rows = json.loads(message['text'])['resize']
                    _set_size(fd, int(cols), int(rows))
                except (ValueError, KeyError, TypeError, OSError):
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        logger.exception('Terminal of {} failed', user.name)
    finally:
        for task in tasks:
            task.cancel()
        try:
            loop.remove_reader(fd)
        except ValueError:
            pass
        await _terminate(proc)
        if proc.returncode not in (0, -signal.SIGHUP, -signal.SIGTERM, -signal.SIGKILL):
            logger.warning('login for {} ended with status {}', user.name, proc.returncode)
        os.close(fd)
        _sessions.discard(pid)
        logger.info('Terminal of {} closed after {:.0f} s', user.name, time.monotonic() - started)
