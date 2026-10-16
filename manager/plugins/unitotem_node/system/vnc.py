"""
The VNC server behind the "look at the screen" window of the web UI.

Nothing here is a service: a server is started when an administrator opens the window and is gone when the window
closes, the websocket drops or nobody touches it for a while (see unitotem_node/vnc.py, which owns the lifecycle).

Why TigerVNC's `x0vncserver` and not x11vnc: it is the one that suits a plain Xorg session with no window manager and no
compositor (it captures through XShm and XDamage and needs nothing from the session), it can listen on a UNIX socket and
nowhere else, and it can ignore keyboard and pointer events by itself, which is what makes view-only a property of the
server and not a request to the browser. Debian ships it as `tigervnc-scraping-server`, binary `X0tigervnc` (the
`x0vncserver` name is an alternatives link that only exists once the package is configured).

The server listens on **no TCP port at all** (`-rfbport -1`): its only door is a UNIX socket inside a directory that is
created with mode 0700 by root and named after a random token, so nothing outside the machine can reach it, nothing on
the machine except root either, and nobody can guess where it is. That is also why it runs with no VNC password: a
VncAuth password is a DES-obfuscated 8 character secret, strictly weaker than the filesystem permission that already
guards the socket, and the browser reaches the socket only through the manager's authenticated admin websocket.
"""
import asyncio
import os
import secrets
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .screens.xorg import XOutput

# Where the per-session directories live. /run is a tmpfs: a crash cannot leave anything behind after a reboot.
RUNTIME_ROOT = Path('/run/unitotem-vnc')
MAX_SESSIONS = 2                # every one captures the screen continuously
# Seconds without the user doing anything in the window before the session is closed; 0 (the default, decided with the user)
# means never: a session lasts as long as the window is open, and ends when it is closed, the connection drops, the server
# dies or the manager stops. The variable switches the limit on (a test, or a node whose owner wants it).
IDLE_TIMEOUT = int(os.environ.get('UNITOTEM_VNC_IDLE_TIMEOUT', 0))
FRAME_RATE = 20                 # the default is 60: a kiosk with a software compositor cannot afford that
BINARY_NAMES = ('x0vncserver', 'X0tigervnc')
BINARY_FALLBACKS = ('/usr/bin/x0vncserver', '/usr/bin/X0tigervnc')


class VncError(Exception):
    """The server could not be started; the message is safe to show to the administrator."""


def find_binary(which: Callable[[str], Optional[str]] = shutil.which, exists=os.path.exists) -> Optional[str]:
    """The VNC server executable, or None when the package is not installed (the window then explains it)."""
    for name in BINARY_NAMES:
        if found := which(name):
            return found
    for path in BINARY_FALLBACKS:
        if exists(path):
            return path
    return None


def new_token() -> str:
    """Names the session directory: 128 random bits, so the socket path cannot be guessed."""
    return secrets.token_urlsafe(16)


def geometry(output: Optional[XOutput]) -> Optional[str]:
    """X geometry (`WxH+X+Y`) of one output, None for the whole desktop. Position is the output's place in the
    virtual screen, which is what x0vncserver's -Geometry is relative to."""
    if output is None:
        return None
    if not (output.connected and output.active and output.width > 0 and output.height > 0):
        raise VncError(f'Output {output.name} is not showing anything')
    return f'{output.width}x{output.height}+{output.x}+{output.y}'


def build_command(binary: str, socket_path: str, control: bool, area: Optional[str] = None) -> list[str]:
    """The command line. Everything that could leak or be abused is switched off, and input is accepted only when the
    administrator asked for control: view-only is enforced here, by the server, not only by the browser."""
    command = [
        binary,
        '-rfbunixpath', socket_path,
        '-rfbport', '-1',                       # no TCP listener, ever
        '-SecurityTypes', 'None',               # see the module docstring
        '-AcceptCutText=0', '-SendCutText=0',   # the clipboard of the totem is nobody's business
        '-AcceptSetDesktopSize=0',              # a client must not be able to change the resolution of the screen
        '-FrameRate', str(FRAME_RATE),
        f'-AcceptKeyEvents={int(control)}',
        f'-AcceptPointerEvents={int(control)}',
    ]
    if area:
        command += ['-Geometry', area]
    return command


class IdleWatch:
    """Counts the time since the user last did something. The clock is a parameter so that it can be tested."""

    def __init__(self, timeout: float = IDLE_TIMEOUT, clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.last = clock()

    def touch(self):
        self.last = self.clock()

    def remaining(self) -> float:
        return self.timeout - (self.clock() - self.last)

    def expired(self) -> bool:
        return self.remaining() <= 0


@dataclass(eq=False)        # kept in a set of live sessions: identity, not field equality
class VncServer:
    """One server, one directory, one socket."""
    binary: str
    control: bool = False
    area: Optional[str] = None
    root: Path = RUNTIME_ROOT
    token: str = field(default_factory=new_token)
    proc: Optional[asyncio.subprocess.Process] = None

    @property
    def directory(self) -> Path:
        return self.root / self.token

    @property
    def socket_path(self) -> str:
        return str(self.directory / 'rfb')

    @property
    def command(self) -> list[str]:
        return build_command(self.binary, self.socket_path, self.control, self.area)

    async def start(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.directory.mkdir(mode=0o700)
        env = {k: v for k, v in os.environ.items() if k in ('DISPLAY', 'XAUTHORITY', 'PATH', 'LANG')}
        try:
            # Its log goes to a file in the session directory (a pipe nobody reads would block it once full); it is
            # read only when the server dies, and removed with the directory.
            with open(self.directory / 'log', 'wb') as log:
                self.proc = await asyncio.create_subprocess_exec(
                    *self.command, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True)
        except OSError as exc:
            self.cleanup()
            raise VncError(f'Cannot start the VNC server: {exc}') from exc
        (self.directory / 'pid').write_text(str(self.proc.pid))

    async def connect(self, timeout: float = 5.0):
        """Open the socket once the server has created it. It has no way of announcing that, so ask until it is there
        (a few tens of milliseconds) or the server has died, whichever comes first."""
        deadline = time.monotonic() + timeout
        while True:
            if self.proc.returncode is not None:
                raise VncError(await self._why_it_died())
            try:
                return await asyncio.open_unix_connection(self.socket_path)
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    raise VncError('The VNC server did not come up')
                await asyncio.sleep(0.05)

    async def _why_it_died(self) -> str:
        try:
            err = (self.directory / 'log').read_text(errors='replace').strip()
        except OSError:
            err = ''
        logger.error('The VNC server exited with status {}: {}', self.proc.returncode, err)
        # No X server (a headless node) or no permission to it are the realistic causes
        return 'The VNC server could not attach to the screen' + (f': {err.splitlines()[-1]}' if err else '')

    async def stop(self):
        """Terminate the server and make sure it is gone, reaped and its directory removed."""
        proc = self.proc
        if proc is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if proc.returncode is not None:
                    break
                try:
                    proc.send_signal(sig)
                except ProcessLookupError:
                    break
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    continue
        self.cleanup()

    def cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def sweep_stale(root: Path = RUNTIME_ROOT, proc_root: Path = Path('/proc')):
    """Remove what a manager that died left behind: a server is a child of the manager, and systemd takes the whole
    control group down when the service stops, but a manager killed by hand must not leave a live capture of the screen
    around. Only a process whose command line mentions the session's own socket is signalled, never a recycled pid."""
    if not root.is_dir():
        return
    for directory in root.iterdir():
        try:
            pid = int((directory / 'pid').read_text())
            cmdline = (proc_root / str(pid) / 'cmdline').read_bytes()
            if str(directory / 'rfb').encode() in cmdline:
                os.kill(pid, signal.SIGKILL)
                logger.warning('Killed a VNC server left behind by a previous run (pid {})', pid)
        except (OSError, ValueError):
            pass
        shutil.rmtree(directory, ignore_errors=True)
