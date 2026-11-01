"""
The guided setup of a touch panel, as a service: it reads the panel's RAW taps from its event node (no compositor and no libinput in between, so
what the compositor does with the panel cannot disturb it), runs a `touch_calibration.Session`, draws the targets on the totem's screen (a message to
the viewer window of the panel's output) and tells the web page every change, so the dialog there mirrors what the totem shows.

Nothing is saved before the visitor says "Yes, keep it": while a result is being tried it is handed to the compositor only, and anything that ends the
setup without that answer (cancel, timeout, the panel unplugged, a new start) puts the stored configuration back.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import struct
import time
from typing import Awaitable, Callable, Optional

from loguru import logger

from . import touch, touch_calibration as tc
from .screens import wayland

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_MT_SLOT, ABS_MT_X, ABS_MT_Y, ABS_MT_ID = 0, 1, 0x2f, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14a
EVIOCGABS = 0x80184540          # + the axis number
EVENT = struct.Struct('qqHHi')
LOCAL_VIEWER = 'local-webview'      # how the viewer of this very node is known to its own manager (utils/remote/bundle.py)


class SetupRefused(Exception):
    """The setup cannot start; the text says what to do."""


class TapDetector:
    """Turns the stream of events of one panel into taps: the position (0..1 of the panel's own range) in the middle of each contact, taken when
    the finger leaves. Only the first contact counts: a second finger during a tap is ignored."""

    def __init__(self, ranges: dict[int, tuple[int, int]]):
        self.ranges = ranges
        self.x: Optional[float] = None
        self.y: Optional[float] = None
        self.slot = 0
        self.samples: list[tuple[float, float]] = []
        self.down = False

    def _norm(self, axis: int, value: int) -> float:
        low, high = self.ranges.get(axis, (0, 4095))
        return min(1.0, max(0.0, (value - low) / max(1, high - low)))

    def feed(self, kind: int, code: int, value: int) -> Optional[tuple[float, float]]:
        """One input event. Returns the position of a tap when this event ends one."""
        if kind == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif self.slot != 0:
                return None
            elif code in (ABS_X, ABS_MT_X):
                self.x = self._norm(code, value)
            elif code in (ABS_Y, ABS_MT_Y):
                self.y = self._norm(code, value)
            elif code == ABS_MT_ID:
                if value == -1:
                    return self._lift()
                self.down = True
        elif kind == EV_KEY and code == BTN_TOUCH:
            if value == 1:
                self.down = True
            elif value == 0:
                return self._lift()
        elif kind == EV_SYN and self.down and self.x is not None and self.y is not None:
            self.samples.append((self.x, self.y))
        return None

    def _lift(self) -> Optional[tuple[float, float]]:
        samples, self.samples, self.down = self.samples, [], False
        if not samples:
            return None
        xs, ys = sorted(s[0] for s in samples), sorted(s[1] for s in samples)
        return (xs[len(xs) // 2], ys[len(ys) // 2])


def read_ranges(fd: int) -> dict[int, tuple[int, int]]:
    """The range each position axis of a panel reports (EVIOCGABS)."""
    ranges = {}
    for axis in (ABS_X, ABS_Y, ABS_MT_X, ABS_MT_Y):
        try:
            buffer = bytearray(24)
            fcntl.ioctl(fd, EVIOCGABS + axis, buffer)
            _value, low, high = struct.unpack_from('iii', buffer)
            if high > low:
                ranges[axis] = (low, high)
        except OSError:
            pass
    return ranges


Notify = Callable[[dict], Awaitable[None]]


class TouchSetup:
    def __init__(self, service):
        self.service = service
        self.session: Optional[tc.Session] = None
        self._fd: Optional[int] = None
        self._detector: Optional[TapDetector] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._notify: Optional[Notify] = None
        self._overlay_output: Optional[str] = None

    def bind(self, notify: Notify):
        """`notify(state)` tells the web pages (one broadcast under Settings/Display/Touch/setup)."""
        self._notify = notify

    # ── what the page asks ──

    def state(self) -> dict:
        return self.session.state() if self.session else {'phase': 'idle'}

    async def start(self, key: str, method: str) -> dict:
        from ..models.touch_map import touch_map
        if method not in ('corners', 'points'):
            raise SetupRefused('The setup is "corners" or "points".')
        await self._end(restore=True)
        device = next((d for d in self.service.devices if d.key == key), None)
        if device is None or not device.node:
            raise SetupRefused('This touch screen is not connected.')
        output = touch_map.mapping.get(key) or (self.service.outputs[0].name if len(self.service.outputs) == 1 else None)
        screen = next((o for o in self.service.outputs if o.name == output), None)
        if screen is None:
            raise SetupRefused('Choose the screen of this touch panel first.')
        try:
            fd = os.open(device.node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            raise SetupRefused(f'The touch panel cannot be read ({exc.strerror}).')
        self._fd = fd
        self._detector = TapDetector(read_ranges(fd))
        self.session = tc.Session(method, key, screen.name, transform=screen.transform)
        asyncio.get_running_loop().add_reader(fd, self._readable)
        self._arm()
        self._overlay_output = screen.name
        await self._publish()
        logger.info('Touch setup ({}) of {} on {}', method, key.split('|')[0], screen.name)
        return self.session.state()

    async def confirm(self) -> dict:
        """The visitor said the dot follows the finger: keep the result."""
        from ..models.touch_map import touch_map
        session = self.session
        if session is None or session.phase != 'try' or session.result is None:
            raise SetupRefused('There is nothing to keep.')
        if 'matrix' in session.result:
            touch_map.set_calibration(session.key, session.result['matrix'])
        else:
            touch_map.set_calibration(session.key, None)
            touch_map.set_orientation(session.key, session.result['turn'], session.result['flip'])
        touch_map.save()
        session.phase = 'saved'
        await self._end(restore=False)
        await self.service.refresh()
        return {'phase': 'saved'}

    async def cancel(self) -> dict:
        await self._end(restore=True)
        return {'phase': 'idle'}

    async def reset(self, key: str) -> None:
        """Back to automatic: what a freshly plugged panel is."""
        from ..models.touch_map import touch_map
        await self._end(restore=False)
        touch_map.set_calibration(key, None)
        touch_map.set_orientation(key, 0, 0)
        touch_map.save()
        await self.service.refresh()

    # ── the raw taps ──

    def _readable(self):
        try:
            data = os.read(self._fd, EVENT.size * 64)
        except (BlockingIOError, OSError):
            return
        for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _u, kind, code, value = EVENT.unpack_from(data, offset)
            point = self._detector.feed(kind, code, value)
            if point is not None and self.session is not None:
                asyncio.get_running_loop().create_task(self._tapped(point))

    async def _tapped(self, point: tuple[float, float]):
        session = self.session
        if session is None:
            return
        before = session.phase
        session.tap(point)
        self._arm()
        if session.phase == 'try' and before != 'try':
            self.service.trial = (session.key, session.matrix())
            await self.service.refresh()                    # the compositor gets the result to try, nothing is stored
        await self._publish()

    def _arm(self):
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(
            tc.STEP_TIMEOUT, lambda: asyncio.get_running_loop().create_task(self._timeout()))

    async def _timeout(self):
        if self.session is not None:
            logger.info('Touch setup ended: no touch for {} s', tc.STEP_TIMEOUT)
            await self._end(restore=True)

    async def panel_gone(self, key: str):
        """The panel was unplugged: a setup of it cannot go on."""
        if self.session is not None and self.session.key == key and not any(d.key == key for d in self.service.devices):
            await self._end(restore=True)

    # ── ending, and what the screens are told ──

    async def _end(self, restore: bool):
        session, self.session = self.session, None
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._fd is not None:
            try:
                asyncio.get_running_loop().remove_reader(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None
        self._detector = None
        had_trial, self.service.trial = self.service.trial, None
        output, self._overlay_output = self._overlay_output, None
        if had_trial and restore:
            await self.service.refresh()
        if output:
            await self._overlay(output, {'phase': 'idle'})
        if self._notify and (session is not None or restore):
            await self._notify({'phase': 'saved' if (session and session.phase == 'saved') else 'idle'})

    async def _publish(self):
        if self.session is None:
            return
        state = self.session.state()
        if self._overlay_output:
            await self._overlay(self._overlay_output, state)
        if self._notify:
            await self._notify(state)

    async def _overlay(self, output: str, state: dict):
        """The state on the totem's screen: the viewer window of that output draws the targets (a node without a viewer simply has none)."""
        try:
            from webview.controller import WebviewManager
            manager = WebviewManager.get_instance()
        except (ImportError, RuntimeError):
            return
        # only the viewer of this node: the panel is on one of ITS outputs, a remote client may have an output of the same name
        info = manager._webviews.get(LOCAL_VIEWER, {})
        for window in info.get('windows', []):
            if window.get('screen_name') == output:
                await manager.send_webview_command(LOCAL_VIEWER, 'SetTouchSetup', window_id=window['window_id'], **state)


from .touch_service import touch_service      # noqa: E402  (the service does not know the setup: no cycle)

touch_setup = TouchSetup(touch_service)
