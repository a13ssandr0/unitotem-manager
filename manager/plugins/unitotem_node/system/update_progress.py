"""
What the updater is doing, told to everybody without anybody asking.

`unitotem-update` writes its progress to ONE file, /run/unitotem-update.progress (one JSON line, replaced atomically), for every long
operation: a download (`fetch`), a `repair` of the other slot (a copy of the running system, started by the updater itself after a boot,
nobody here asked for it) and the rest. Watching that file (inotify on /run, no polling) is how the web UI learns about an operation it
did not start:

 * the Updates page gets the status pushed (`Settings/Update/status`), so its progress bar moves;
 * a PERSISTENT notification with a progress bar (`update.progress`, in the bell, level `error`: the highest level, a slot write must not
   go unnoticed) lives as long as the operation runs and cannot be dismissed meanwhile;
 * when it ends the bar goes away, and what came of it is told once: a failure as `update.failed` (error), a repair as `update.repaired`
   (info), a download as `update.fetched` (info).

The file is only a hint of where to look: whether an operation is really running is what `status` says (`op`), because the file stays
in /run after the operation ended, and the updater being killed leaves a "running" file behind. The updater releases its lock after the
last write of the file, so the end is looked at once more, a second later, when the last state written is a final one.
"""
import asyncio
import calendar
import ctypes
import math
import os
import struct
import time
from typing import Awaitable, Callable, Optional

from loguru import logger

from utils.notifications import notifications
from . import updater

RUN_DIR = '/run'
PROGRESS_FILE = 'unitotem-update.progress'
IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE = 0x8, 0x80, 0x100
MIN_INTERVAL = 1.0          # the status (a subprocess) is read at most once a second while the file changes
LATE_REFRESH = 1.0          # the updater releases its lock after its last write: one more look, this long after a final state
KEY = 'update.progress'
LEVEL = 'error'             # the highest level there is: a slot copy is not something to miss
ENDED = ('done', 'failed', 'cancelled')
UPDATED_AT = '%Y-%m-%dT%H:%M:%SZ'     # the updater's `at` (UTC)
MB = 1000 * 1000

PHASES = {'check': 'Checking', 'download': 'Downloading', 'write': 'Writing', 'verify': 'Verifying', 'arm': 'Preparing the restart',
          'reboot': 'Restarting'}

OnStatus = Callable[[dict], Awaitable[None]]


def result_time(at) -> Optional[float]:
    """The time of the updater's result (`at`, UTC, '%Y-%m-%dT%H:%M:%SZ') in epoch seconds, or None when it is not that."""
    if not isinstance(at, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(at, UPDATED_AT)))
    except ValueError:
        return None


# Why the updater rebuilt a slot (`cause` of the repair progress in unitotem-update): a slot that is damaged is not the same thing as one
# that was never a complete system (a recovery, an interrupted download), and neither is an update. first-boot and unprepared are copies.
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


def is_rebuild(cause: Optional[str]) -> bool:
    """True when the cause says the slot was damaged or was never a complete system (a rebuild, not a copy)."""
    return cause in DAMAGED or cause == 'empty' or (cause or '').startswith('bootfile:')


def copy_source(cause: Optional[str], running: Optional[str]) -> Optional[str]:
    """The slot a repair copied from, or None when it rebuilt a slot. first-boot, unprepared and a repair without a cause (a forced
    copy) copy the running slot."""
    return None if is_rebuild(cause) else running


def size_caption(progress: dict) -> Optional[str]:
    """'NN / NN MB' of a write, or None unless both sizes are sane numbers (the same rule as the Updates page)."""
    done, total = progress.get('bytes'), progress.get('total')
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (done, total)):
        return None
    if total <= 0 or done < 0 or done > total:
        return None
    return f'{round(done / MB)} / {round(total / MB)} MB'


def phase_text(op: str, phase: str, size: Optional[str] = None) -> str:
    """The phase in words. A write shows its size when it is known; a repair write is a copy, which the title already says: ''."""
    if phase == 'write' and size:
        return size
    if op == 'repair' and phase == 'write':
        return ''
    return PHASES.get(phase, phase or 'Working')


def describe(op: str, phase: str, slot: Optional[str], cause: Optional[str] = None, copy_from: Optional[str] = None,
             size: Optional[str] = None) -> tuple[str, str]:
    """(title, what is happening now) of an operation, with slot names in capitals."""
    if op == 'repair' and copy_from:
        return f'Copying slot {copy_from} to slot {slot}', phase_text(op, phase, size)
    if op == 'repair':
        what = 'Rebuilding' + (f' slot {slot}' if slot else ' the other slot')
        title = what + (' after damage' if cause and (cause in DAMAGED or cause.startswith('bootfile:')) else
                        ' (incomplete system)' if cause == 'empty' else '')
        return title, phase_text(op, phase, size)
    titles = {'fetch': 'Downloading the update', 'apply': 'Installing the update', 'rollback': 'Going back to the previous version'}
    return titles.get(op, f'Update: {op}'), phase_text(op, phase, size)


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
        self._late: Optional[asyncio.TimerHandle] = None
        self._last = 0.0
        self._seen: Optional[str] = None       # the operation whose notification is up

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
        if self._late:
            self._late.cancel()

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

    def _schedule_late(self):
        """One more look a second after a final state (see LATE_REFRESH); a single one is pending at a time."""
        if self._late is None:
            self._late = self._loop.call_later(LATE_REFRESH, lambda: self._loop.create_task(self._run(late=True), name='update_progress'))

    async def _run(self, late: bool = False):
        if not late:
            self._timer = None
        try:
            await self.refresh(late=late)
        except Exception:
            logger.exception('Update progress failed')

    async def refresh(self, late: bool = False):
        if late:
            self._late = None
        self._last = self._loop.time()
        status = await updater.status()
        if not status.get('supported'):
            return
        op = status.get('op')
        progress = (op or {}).get('progress') or {}
        if op:
            name = progress.get('op') or 'update'
            source = status.get('copy_from') or (copy_source(progress.get('cause'), status.get('slot')) if name == 'repair' else None)
            title, phase = describe(name, progress.get('phase', ''), status.get('other_slot'), progress.get('cause'), source,
                                    size_caption(progress))
            percent = -1 if progress.get('percent') is None else progress['percent']
            text = ', '.join(x for x in (phase, f"about {progress['eta_s']} s left" if progress.get('eta_s') else '') if x)
            self._raise_bar(title, percent, text, name)
            self._seen = name
            if progress.get('state') in ENDED and not late:
                self._schedule_late()
        elif self._seen is not None:
            await self._finished(self._seen, status)
        else:
            bar = notifications.get(KEY)
            if bar is not None:     # a bar left by a manager that went down in the middle of an operation: what it ended with is told
                await self._finished(None, status, since=bar['created'])
        if self._on_status:
            await self._on_status(status)

    def _raise_bar(self, title: str, percent: float, text: str, name: str):
        """The bar: raised when it is new, changed in title or level, or of another operation; otherwise only moved (set_progress keeps
        it read)."""
        bar = notifications.get(KEY)
        if bar is None or (bar['level'], bar['title']) != (LEVEL, title) or self._seen != name \
                or not notifications.set_progress(KEY, percent, text):
            notifications.notify(KEY, LEVEL, title, text, source='updates', audience='admin', link='/settings/updates',
                                 progress=percent, persistent=True)

    async def _finished(self, name: Optional[str], status: dict, since: Optional[float] = None):
        """The operation is over: the bar goes, and what came of it is told once.

        `name` is the operation the bar was raised for, None after a manager restart. Then the last result is told only when it is a
        fetch or a repair, and when its time (`at`, UTC) is known and not older than the bar (`since`)."""
        self._seen = None
        notifications.resolve(KEY)
        result = status.get('last_result') or {}
        op = result.get('op')
        if name is None:
            at = result_time(result.get('at'))
            if op not in ('fetch', 'repair') or (since is not None and at is not None and at < since):
                return
            name = op
        if op != name:
            return
        slot = result.get('slot')
        if result.get('result') in ('failed', 'error'):
            if name == 'repair':
                title = 'Copying the slot failed' if copy_source(result.get('cause'), status.get('slot')) else 'Rebuilding the slot failed'
            else:
                title = 'Update failed'
            notifications.notify('update.failed', 'error', title, result.get('error') or 'The operation did not complete.',
                                 source='updates', audience='admin', link='/settings/updates')
        elif result.get('result') == 'ok':
            notifications.resolve('update.failed')
            if name == 'repair':
                copied = copy_source(result.get('cause'), status.get('slot'))
                if copied:
                    notifications.notify('update.repaired', 'info', f'Slot {slot} copied',
                                         f'Slot {slot} is now a copy of slot {copied}.', source='updates', audience='admin',
                                         link='/settings/updates')
                else:
                    why = cause_text(result.get('cause'))
                    notifications.notify('update.repaired', 'info', f'Slot {slot} rebuilt' if slot else 'Slot rebuilt',
                                         'The other system slot was rebuilt from the running one' + (f' because {why}.' if why else '.'),
                                         source='updates', audience='admin', link='/settings/updates')
            elif name == 'fetch':
                version = updater.availability.get('version')
                notifications.notify('update.fetched', 'info', 'Update downloaded',
                                     f'Version {version} is ready to install' if version else 'The update is ready to install.',
                                     source='updates', audience='admin', link='/settings/updates')


update_progress = UpdateProgress()
