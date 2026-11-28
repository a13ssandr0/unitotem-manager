"""
What the firmware helper is doing, told to everybody without anybody asking.

`unitotem-firmware` writes its progress to /run/unitotem-firmware.progress (one JSON line, replaced atomically). Watching that file
(inotify on /run, shared with update_progress.py) lets the web UI learn about an operation it did not start:

 * the Updates page gets the status pushed (`Settings/Firmware/status`);
 * a persistent notification with a progress bar (`firmware.progress`) lives as long as the operation runs;
 * when it ends the bar goes away; a failure is raised as `firmware.failed` (error), a finished apply as `firmware.done`
   (information, with the restart text when the device must restart).

The file is only a hint of where to look: whether an operation runs is what `status` says (`op`).
"""
import asyncio
import os
from typing import Awaitable, Callable, Optional

from loguru import logger

from utils.notifications import notifications
from . import firmware, restart
from .update_progress import RUN_DIR, _inotify, names_in

PROGRESS_FILE = 'unitotem-firmware.progress'
MIN_INTERVAL = 1.0          # the status (a subprocess) is read at most once a second while the file changes
KEY = 'firmware.progress'

PHASES = {'fetch': 'Downloading', 'verify': 'Verifying', 'stage': 'Preparing', 'swap': 'Writing', 'eeprom': 'Updating the EEPROM',
          'done': 'Finishing'}
TITLES = {'check': 'Checking the firmware', 'apply': 'Updating the firmware'}

OnStatus = Callable[[dict], Awaitable[None]]


class FirmwareProgress:
    def __init__(self):
        self._fd: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_status: Optional[OnStatus] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._last = 0.0
        self._seen: Optional[str] = None       # the operation whose notification is up

    async def start(self, on_status: OnStatus):
        self._loop = asyncio.get_running_loop()
        self._on_status = on_status
        self._fd = _inotify(RUN_DIR)
        if self._fd is not None:
            self._loop.add_reader(self._fd, self._readable)
        else:
            logger.warning('Cannot watch {}: firmware progress will only show on the Updates page', RUN_DIR)
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
        self._timer = self._loop.call_later(wait, lambda: self._loop.create_task(self._run(), name='firmware_progress'))

    async def _run(self):
        self._timer = None
        try:
            await self.refresh()
        except Exception:
            logger.exception('Firmware progress failed')

    async def refresh(self):
        self._last = self._loop.time()
        status = await firmware.status()
        if not status.get('supported'):
            return
        op = status.get('op')
        if op:
            percent = status.get('percent')
            phase = PHASES.get(status.get('phase') or '', 'Working')
            if notifications.set_progress(KEY, -1 if percent is None else percent, phase) is False or self._seen != op:
                notifications.notify(KEY, 'info', TITLES.get(op, 'Firmware'), phase, source='updates', audience='admin',
                                     link='/settings/updates', progress=-1 if percent is None else percent, persistent=True)
            self._seen = op
        elif self._seen is not None:
            await self._finished(self._seen, status)
        else:
            notifications.resolve(KEY)      # a bar left by a manager that went down in the middle of an operation
        if self._on_status:
            await self._on_status(status)

    async def _finished(self, op: str, status: dict):
        """The operation the notification was about is over: the bar goes, and what came of it is told."""
        self._seen = None
        notifications.resolve(KEY)
        restart_asked = firmware._restart_requested
        firmware.request_restart(False)       # every end clears the request; a started restart sets it again
        if 'restart_pending' in status:
            status['restart_pending'] = False
        error = status.get('error')
        if error:
            notifications.notify('firmware.failed', 'error', 'Firmware update failed',
                                 error.get('error') or 'The firmware update did not complete.', source='updates',
                                 audience='admin', link='/settings/updates')
        elif op == 'apply':
            notifications.resolve('firmware.failed')
            reboot = bool(status.get('reboot_required'))
            automatic = reboot and restart_asked
            text = ('The device restarts now.' if automatic else
                    'Restart the device to start the new firmware.' if reboot else 'The firmware is up to date.')
            notifications.notify('firmware.done', 'info', 'Firmware updated', text, source='updates', audience='admin',
                                 link='/settings/updates')
            if automatic:
                # the state stays "restart pending" until the manager goes down with the device
                firmware.request_restart(True)
                status['restart_pending'] = True
                try:
                    await restart.restart()
                except Exception:
                    logger.exception('Restart after the firmware update failed')
                    firmware.request_restart(False)
                    status['restart_pending'] = False
                    notifications.notify('firmware.done', 'info', 'Firmware updated',
                                         'Restart the device to start the new firmware.', source='updates',
                                         audience='admin', link='/settings/updates')

firmware_progress = FirmwareProgress()
