"""
Keeps the touch mapping applied. Event-driven: a connection to the X server listens for XInput2 hierarchy events (a
device was added or removed) and RandR notifications (an output appeared, went away, moved or rotated) and, a moment
after the last of a burst, writes the matrices again and tells the open pages. Nothing polls.

No X display, or an X server without XInput, simply leaves the list empty.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from loguru import logger

from . import touch

DEBOUNCE = 0.3          # a hot-plug is a burst of events; the matrices are computed once it settles
CONNECT_ATTEMPTS = 30   # the manager can start a moment before X is up
CONNECT_DELAY = 2.0

OnChange = Callable[[], Awaitable[None]]


class TouchService:
    def __init__(self):
        self.x: Optional[touch.XTouch] = None
        self.devices: list[touch.TouchDevice] = []
        self.outputs: list[touch.Output] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._on_change: Optional[OnChange] = None

    async def start(self, on_change: OnChange):
        """Connect (waiting a little for X to come up), apply the stored mapping and start listening."""
        self._loop = asyncio.get_running_loop()
        self._on_change = on_change
        if not os.environ.get('DISPLAY'):
            logger.debug('No X display: touch mapping disabled')
            return
        for _ in range(CONNECT_ATTEMPTS):
            try:
                self.x = touch.XTouch()
                self.x.select_events()
                break
            except ImportError:
                logger.debug('python-xlib is missing: touch mapping disabled')
                return
            except Exception as exc:
                logger.debug('Touch mapping cannot reach the X server yet: {}', exc)
                if self.x:
                    self.x.close()
                self.x = None
                await asyncio.sleep(CONNECT_DELAY)
        if self.x is None:
            logger.warning('Touch mapping disabled: cannot connect to the X server')
            return
        self._loop.add_reader(self.x.fileno(), self._readable)
        await self.refresh()

    def stop(self):
        if self._timer:
            self._timer.cancel()
        if self.x and self._loop:
            try:
                self._loop.remove_reader(self.x.fileno())
            except Exception:
                pass
            self.x.close()
            self.x = None

    # events -----------------------------------------------------------------

    def _readable(self):
        try:
            if self.x.relevant_events():
                self._schedule()
        except Exception as exc:        # the X server went away: the kiosk is going down with it
            logger.warning('Lost the X connection of the touch mapping: {}', exc)
            self.stop()

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
        """Apply the stored mapping and remember what is there."""
        from ..models.touch_map import touch_map
        if self.x is None:
            return
        try:
            self.x.relevant_events()        # events queued by our own round trips are not news
            self.devices, self.outputs = touch.apply_mapping(self.x, touch_map.mapping)
            if self.x.relevant_events():    # something changed while we were writing
                self._schedule()
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
            'mapped': not touch.close_enough(d.matrix, touch.IDENTITY),
        } for d in self.devices]
        for key, output in touch_map.mapping.items():
            if key not in live:
                devices.append({'key': key, 'name': key.split('|')[0], 'vendor': None, 'product': None,
                                'serial': None, 'output': output, 'connected': False, 'mapped': False})
        return {
            'devices': devices,
            'outputs': [{'name': o.name, 'x': o.x, 'y': o.y, 'width': o.width, 'height': o.height,
                         'rotation': o.rotation_name} for o in self.outputs],
            'available': self.x is not None,
        }


touch_service = TouchService()
