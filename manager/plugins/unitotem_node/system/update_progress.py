"""
What the updater is doing, told to everybody without anybody asking.

`unitotem-update` writes its progress to ONE file, /run/unitotem-update.progress (one JSON line, replaced atomically), for every long
operation: a download (`fetch`), a `repair` of the other slot (started by the updater itself after a boot, nobody here asked for it) and
the rest. Watching that file (inotify on /run, no polling) is how the web UI learns about an operation it did not start:

 * the Updates page gets the status pushed (`Settings/Update/status`), so its progress bar moves;
 * a PERSISTENT notification with a progress bar (`update.progress`, in the bell) lives as long as the operation runs;
 * when it ends the bar goes away; a failure is raised as an error notification (`update.failed`) and a repair that finished as an
   information (`update.repaired`).

The file is only a hint of where to look: whether an operation is really running is what `status` says (`op`), because the file stays
in /run after the operation ended, and the updater being killed leaves a "running" file behind.
"""
import asyncio
import ctypes
import os
import struct
from typing import Awaitable, Callable, Optional

from loguru import logger

from utils.notifications import notifications
from . import updater

RUN_DIR = '/run'
PROGRESS_FILE = 'unitotem-update.progress'
IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE = 0x8, 0x80, 0x100
MIN_INTERVAL = 1.0          # the status (a subprocess) is read at most once a second while the file changes
KEY = 'update.progress'

PHASES = {'check': 'Checking', 'download': 'Downloading', 'write': 'Writing', 'verify': 'Verifying', 'arm': 'Preparing the restart',
          'reboot': 'Restarting'}

OnStatus = Callable[[dict], Awaitable[None]]


# Why the updater rebuilt a slot (`cause` of `slot_sound` in unitotem-update): a slot that is damaged is not the same thing as one that was
# never a complete system (a recovery, an interrupted download), and neither is an update.
DAMAGED = ('label', 'fsck', 'mount')
CAUSES = {'label': 'its partition label is wrong', 'fsck': 'its filesystem is damaged', 'mount': 'it cannot be mounted',
          'empty': 'it did not hold a complete system'}


def cause_text(cause: Optional[str]) -> Optional[str]:
    """The updater's `cause` of a repair in words, or None when it did not say."""
    if not cause:
        return None
    if cause.startswith('bootfile:'):
        return f'its boot file {cause.split(":", 1)[1]} is missing'
    return CAUSES.get(cause)


def describe(op: str, phase: str, slot: Optional[str], cause: Optional[str] = None, copy_from: Optional[str] = None) -> tuple[str, str]:
    """(title, what is happening now) of an operation, with slot names in capitals."""
    if op == 'repair' and copy_from:
        return f'Copying slot {copy_from} to slot {slot}', PHASES.get(phase, phase or 'Working')
    if op == 'repair':
        what = 'Rebuilding' + (f' slot {slot}' if slot else ' the other slot')
        title = what + (' after damage' if cause and (cause in DAMAGED or cause.startswith('bootfile:')) else
                        ' (incomplete system)' if cause == 'empty' else '')
        return title, PHASES.get(phase, phase or 'Working')
    titles = {'fetch': 'Downloading the update', 'apply': 'Installing the update', 'rollback': 'Going back to the previous version'}
    return titles.get(op, f'Update: {op}'), PHASES.get(phase, phase or 'Working')


def _inotify(path: str) -> Optional[int]:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0 or libc.inotify_add_watch(fd, path.encode(), IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE) < 0:
            if fd >= 0:
                os.close(fd)
            return None
        return fd
    except (OSError, AttributeError):
        return None


def names_in(data: bytes) -> list[str]:
    """The file names of the events in a read() of an inotify descriptor."""
    names, at = [], 0
    while at + 16 <= len(data):
        _wd, _mask, _cookie, length = struct.unpack_from('iIII', data, at)
        names.append(data[at + 16:at + 16 + length].split(b'\0', 1)[0].decode(errors='replace'))
        at += 16 + length
    return names


class UpdateProgress:
    def __init__(self):
        self._fd: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_status: Optional[OnStatus] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._last = 0.0
        self._seen: Optional[str] = None       # the operation whose notification is up
        self._copy_from: Optional[str] = None  # set while that operation is a forced copy

    async def start(self, on_status: OnStatus):
        self._loop = asyncio.get_running_loop()
        self._on_status = on_status
        self._fd = _inotify(RUN_DIR)
        if self._fd is not None:
            self._loop.add_reader(self._fd, self._readable)
        else:
            logger.warning('Cannot watch {}: update progress will only show on the Updates page', RUN_DIR)
        await self.refresh()      # an operation that was already running when the manager started

    def stop(self):
        if self._fd is not None and self._loop:
            self._loop.remove_reader(self._fd)
            os.close(self._fd)
            self._fd = None
        if self._timer:
            self._timer.cancel()

    def _readable(self):
        try:
            data = os.read(self._fd, 8192)
        except (BlockingIOError, OSError):
            return
        if PROGRESS_FILE in names_in(data):
            self._schedule()

    def _schedule(self):
        """Trailing throttle: the last state of a burst always gets through."""
        if self._timer is not None:
            return
        wait = max(0.0, self._last + MIN_INTERVAL - self._loop.time())
        self._timer = self._loop.call_later(wait, lambda: self._loop.create_task(self._run(), name='update_progress'))

    async def _run(self):
        self._timer = None
        try:
            await self.refresh()
        except Exception:
            logger.exception('Update progress failed')

    async def refresh(self):
        self._last = self._loop.time()
        status = await updater.status()
        if not status.get('supported'):
            return
        op = status.get('op')
        progress = (op or {}).get('progress') or {}
        if op:
            name = progress.get('op') or 'update'
            title, phase = describe(name, progress.get('phase', ''), status.get('other_slot'), progress.get('cause'),
                                  status.get('copy_from'))
            percent = progress.get('percent')
            text = phase + (f", about {progress['eta_s']} s left" if progress.get('eta_s') else '')
            if notifications.set_progress(KEY, -1 if percent is None else percent, text) is False \
                    or self._seen != name:
                notifications.notify(KEY, 'info', title, text, source='updates', audience='admin', link='/settings/updates',
                                     progress=-1 if percent is None else percent, persistent=True)
            self._seen = name
            self._copy_from = status.get('copy_from')
        elif self._seen is not None:
            await self._finished(self._seen, status)
        else:
            notifications.resolve(KEY)      # a bar left by a manager that went down in the middle of an operation
        if self._on_status:
            await self._on_status(status)

    async def _finished(self, name: str, status: dict):
        """The operation the notification was about is over: the bar goes, and what came of it is told."""
        self._seen = None
        notifications.resolve(KEY)
        result = status.get('last_result') or {}
        if name == 'repair' and result.get('op') == 'repair':
            slot = result.get('slot')
            why = cause_text(result.get('cause'))
            copied, self._copy_from = self._copy_from, None
            if result.get('result') == 'ok' and copied:
                notifications.resolve('update.failed')
                notifications.notify('update.repaired', 'info', f'Slot {slot} copied', f'Slot {slot} is now a copy of slot {copied}.',
                                     source='updates', audience='admin', link='/settings/updates')
            elif result.get('result') == 'ok':
                notifications.resolve('update.failed')
                notifications.notify('update.repaired', 'info', f'Slot {slot} rebuilt' if slot else 'Slot rebuilt',
                                     'The other system slot was rebuilt from the running one' + (f' because {why}.' if why else '.'),
                                     source='updates',
                                     audience='admin', link='/settings/updates')
            elif result.get('result') == 'failed':
                notifications.notify('update.failed', 'error', 'Copying the slot failed' if copied else 'Rebuilding the slot failed',
                                     result.get('error') or 'The copy did not complete.',
                                     source='updates', audience='admin', link='/settings/updates')
        elif result.get('op') == name and result.get('result') in ('failed', 'error'):
            notifications.notify('update.failed', 'error', 'Update failed', result.get('error') or 'The operation did not complete.',
                                 source='updates', audience='admin', link='/settings/updates')


update_progress = UpdateProgress()
