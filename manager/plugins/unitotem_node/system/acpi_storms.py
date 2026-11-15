"""
The ACPI events the storm guard masked, told to everybody without anybody asking.

`unitotem-acpi-storm-guard` writes one line per masked event to /var/lib/unitotem/acpi-storms (replaced atomically):

    gpeNN <first_seen> <last_seen> <rate> <boots> <boot_id> <masked>

`first_seen`/`last_seen` are epoch seconds as the device clock said, `rate` the highest events per second, `boots` the boots in which
the event was masked, `boot_id` the boot that last saw it and `masked` 1 when it was masked in that boot. The file is state, not
configuration: the file is read in full (a few lines), never trusted: an oversized, binary, non-UTF-8 or malformed file reads as empty.

 * the Info page gets the list pushed (`Settings/AcpiStorms/list`) when it changes, and reads it on load (the same call);
 * each entry seen in this boot raises a warning notification (`acpi.storm.<name>.<boots>`): one per event per boot. An entry
   from an earlier boot is shown on the Info page only.

Watch: inotify on the directory of the file (no polling), trailing throttle, the same helpers as update_progress.py.
"""
import asyncio
import os
import re
import stat
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger

from utils.notifications import notifications
from .update_progress import _inotify, names_in

STORMS_FILE = Path('/var/lib/unitotem/acpi-storms')
BOOT_ID_FILE = Path('/proc/sys/kernel/random/boot_id')
MAX_BYTES = 4096
MAX_LINES = 64
MAX_EPOCH = 4102444800
MAX_RATE = 10 ** 9
MAX_BOOTS = 10 ** 6
MIN_INTERVAL = 1.0          # the file is read at most once a second while it changes

LINE = re.compile(r'(gpe[0-9A-F]{2}) (0|[1-9][0-9]{0,9}) (0|[1-9][0-9]{0,9}) (0|[1-9][0-9]{0,9}) (0|[1-9][0-9]{0,6}) '
                  r'([0-9a-f-]{1,40}) ([01])')
BOOT_ID = re.compile(r'[0-9a-f-]{1,40}')

OnState = Callable[[list], Awaitable[None]]


def _read(path: Path, limit: int = MAX_BYTES) -> Optional[bytes]:
    """The whole regular file, or None when it is absent, not a regular file, a symlink, or larger than `limit`.
    Opened without blocking, so a FIFO planted at the path cannot hang the manager."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, limit + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b''.join(chunks) if total <= limit else None
    except OSError:
        return None
    finally:
        os.close(fd)


def current_boot_id() -> Optional[str]:
    data = _read(BOOT_ID_FILE, 64)
    if data is None:
        return None
    try:
        boot_id = data.decode('ascii').strip()
    except UnicodeDecodeError:
        return None
    return boot_id if BOOT_ID.fullmatch(boot_id) else None


def parse(data: bytes, boot_id: Optional[str]) -> list[dict]:
    """The entries of the file, sorted by name. Any file-wide fault (size, NUL, not UTF-8, too many lines) gives []; a line that
    does not match is skipped; of two lines for one event the first one counts."""
    if len(data) > MAX_BYTES or b'\0' in data:
        return []
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return []
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if len(lines) > MAX_LINES:
        return []
    entries: dict[str, dict] = {}
    for line in lines:
        match = LINE.fullmatch(line)
        if match is None:
            continue
        name, first, last, rate, boots, line_boot, masked = match.groups()
        first, last, rate, boots = int(first), int(last), int(rate), int(boots)
        if first > MAX_EPOCH or last > MAX_EPOCH or rate > MAX_RATE or boots > MAX_BOOTS:
            continue
        if name in entries:
            continue
        seen_now = boot_id is not None and line_boot == boot_id
        entries[name] = {'name': name, 'first_seen': first, 'last_seen': last, 'rate': rate, 'boots': boots,
                         'masked_now': seen_now and masked == '1', 'seen_now': seen_now}
    return [entries[name] for name in sorted(entries)]


def read_storms() -> list[dict]:
    """The current state; [] when there is no record. Never raises."""
    try:
        data = _read(STORMS_FILE)
        if data is None:
            return []
        return parse(data, current_boot_id())
    except Exception:
        logger.exception('Cannot read the ACPI storm record')
        return []


class AcpiStorms:
    def __init__(self):
        self._fd: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_state: Optional[OnState] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._last = 0.0
        self._pushed: Optional[list] = None     # the last list sent to the pages

    async def start(self, on_state: OnState):
        self._loop = asyncio.get_running_loop()
        self._on_state = on_state
        self._fd = _inotify(str(STORMS_FILE.parent))
        if self._fd is not None:
            self._loop.add_reader(self._fd, self._readable)
        else:
            logger.warning('Cannot watch {}: ACPI storms will only show when the Info page loads', STORMS_FILE.parent)
        await self.refresh()      # a record left by an earlier start of the manager

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
        if STORMS_FILE.name in names_in(data):
            self._schedule()

    def _schedule(self):
        """Trailing throttle: the last state of a burst always gets through."""
        if self._timer is not None:
            return
        wait = max(0.0, self._last + MIN_INTERVAL - self._loop.time())
        self._timer = self._loop.call_later(wait, lambda: self._loop.create_task(self._run(), name='acpi_storms'))

    async def _run(self):
        self._timer = None
        try:
            await self.refresh()
        except Exception:
            logger.exception('ACPI storm watch failed')

    async def refresh(self):
        self._last = self._loop.time()
        storms = read_storms()
        self._announce(storms)
        if storms != self._pushed:
            self._pushed = storms
            if self._on_state:
                await self._on_state(storms)

    def _announce(self, storms: list[dict]):
        """One warning per event per boot: the key carries the boot count, and the notification center ignores the same
        content raised again."""
        for storm in storms:
            if not storm['seen_now']:
                continue
            name, rate = storm['name'], storm['rate']
            if storm['masked_now']:
                title = f'ACPI event {name} masked'
                text = f'ACPI event {name} was firing {rate} times a second and has been masked'
            else:
                title = f'ACPI event {name} cannot be masked'
                text = f'ACPI event {name} fires {rate} times a second and cannot be masked'
            notifications.notify(f"acpi.storm.{name}.{storm['boots']}", 'warning', title, text, source='acpi-storm-guard',
                                 audience='admin', link='/info')


acpi_storms = AcpiStorms()
