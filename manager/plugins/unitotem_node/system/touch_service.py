"""
Keeps the touch mapping applied and the list of touch screens up to date. Event-driven: the mapping is a fragment of the
compositor configuration, written whenever it changes and at start (the compositor applies it to a device when the device
appears, so a screen plugged later is mapped without anybody noticing), and the kernel's own notifications (inotify on
/dev/input) tell when a device appears or goes away so that the open pages are told. Nothing polls.

No compositor, or no /dev/input, simply leaves the list empty.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import struct
from typing import Awaitable, Callable, Optional

from loguru import logger

from . import touch
from .screens import wayland

DEBOUNCE = 0.3          # a hot-plug is a burst of events; the list is read once it settles
IN_CREATE, IN_DELETE = 0x100, 0x200
INPUT_DIR = '/dev/input'

OnChange = Callable[[], Awaitable[None]]


def _inotify(path: str) -> Optional[int]:
    """A non-blocking inotify descriptor watching `path` for devices appearing and going away (None when it cannot)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0 or libc.inotify_add_watch(fd, path.encode(), IN_CREATE | IN_DELETE) < 0:
            if fd >= 0:
                os.close(fd)
            return None
        return fd
    except (OSError, AttributeError):
        return None


class TouchService:
    def __init__(self):
        self.devices: list[touch.TouchDevice] = []
        self.outputs: list[wayland.Output] = []
        self.available = False
        self._fd: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._on_change: Optional[OnChange] = None

    async def start(self, on_change: OnChange):
        """Apply the stored mapping and start listening for devices."""
        self._loop = asyncio.get_running_loop()
        self._on_change = on_change
        self._fd = _inotify(INPUT_DIR)
        if self._fd is not None:
            self._loop.add_reader(self._fd, self._readable)
        await self.refresh()

    def stop(self):
        if self._timer:
            self._timer.cancel()
        if self._fd is not None and self._loop:
            try:
                self._loop.remove_reader(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None

    # events -----------------------------------------------------------------

    def _readable(self):
        try:
            data = os.read(self._fd, 4096)
        except BlockingIOError:
            return
        # only the event nodes matter (mouse0, js0... are not touch screens)
        offset, relevant = 0, False
        while offset + 16 <= len(data):
            _wd, _mask, _cookie, length = struct.unpack_from('iIII', data, offset)
            name = data[offset + 16:offset + 16 + length].split(b'\0', 1)[0]
            relevant = relevant or name.startswith(b'event')
            offset += 16 + length
        if relevant:
            self._schedule()

    def outputs_changed(self):
        """A screen appeared or went away (told by the viewer, from its own thread): the automatic mapping depends on how many there are."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._schedule)

    def _schedule(self):
        if self._timer:
            self._timer.cancel()
        self._timer = self._loop.call_later(DEBOUNCE, lambda: self._loop.create_task(self._changed()))

    async def _changed(self):
        await self.refresh()
        if self._on_change:
            await self._on_change()

    # state ------------------------------------------------------------------

    async def refresh(self):
        """Write the stored mapping and remember what is there."""
        from ..models.touch_map import touch_map
        try:
            self.devices = await asyncio.to_thread(touch.touch_devices)
            self.outputs = [o for o in await wayland.outputs() if o.active]
            mapping = touch.effective_mapping(touch_map.mapping, self.devices, [o.name for o in self.outputs])
            self.available = await asyncio.to_thread(touch.apply_mapping, mapping) or bool(self.outputs)
        except Exception as exc:
            logger.warning('Cannot apply the touch mapping: {}', exc)

    def snapshot(self) -> dict:
        """What the Display page shows: the devices found (with the output each is mapped to), the ones remembered but
        not plugged in, and the outputs one can choose from."""
        from ..models.touch_map import touch_map
        live = {d.key for d in self.devices}
        devices = [{
            'key': d.key, 'name': d.name, 'vendor': d.vendor, 'product': d.product, 'serial': d.serial,
            'output': touch_map.mapping.get(d.key), 'connected': True,
        } for d in self.devices]
        for key, output in touch_map.mapping.items():
            if key not in live:
                devices.append({'key': key, 'name': key.split('|')[0], 'vendor': None, 'product': None,
                                'serial': None, 'output': output, 'connected': False})
        return {
            'devices': devices,
            'outputs': [{'name': o.name, 'x': o.x, 'y': o.y, 'width': o.width, 'height': o.height,
                         'rotation': o.rotation} for o in self.outputs],
            'available': self.available,
        }


touch_service = TouchService()
