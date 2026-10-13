"""
Idle handling of the windows of a touch panel, and the clock of scheduled refreshes.

Two things hang on "nobody touched this window for a while": the playlist appearing over the home page of an interactive
window (the screensaver), and the reload of a plain web page. The pages in the iframes are cross-origin, so what is
known is only what the injected script of cdp.py reports: one message per second at most per frame while there is
activity. `IdleState` is the decision (pure, driven by a clock it is given, so it can be tested without waiting) and
`IdleWatch` runs it on the asyncio loop with a single timer armed for the earliest deadline: no polling.

Scheduled refreshes use cron-style schedules, evaluated with croniter like the timers of the node plugin.
"""
import asyncio
import time
from datetime import datetime
from typing import Awaitable, Callable, Iterable, Optional

from croniter import croniter
from loguru import logger

SAVER = 'saver'
REFRESH = 'refresh'
_EPSILON = 0.005


class IdleState:
    """What is due, given when the window was last used. Times are on whatever monotonic clock the caller uses."""

    def __init__(self, now: float = 0.0):
        self.saver_timeout: Optional[float] = None      # None: no screensaver
        self.refresh_idle: Optional[float] = None       # None: no refresh on idle
        self.activity = now
        self.refreshed = now
        self.saver_tried = now
        self.overlay = False        # the playlist is on top of the home page
        self.saver_pending = False  # the page has been asked to show it, its answer is not in yet

    def configure(self, saver_timeout: Optional[float], refresh_idle: Optional[float], now: float):
        self.saver_timeout = saver_timeout or None
        # a window with a screensaver is interactive: its pages are not reloaded behind the visitor's back
        self.refresh_idle = None if self.saver_timeout else (refresh_idle or None)
        self.activity = self.refreshed = self.saver_tried = now
        self.overlay = self.saver_pending = False

    def touch(self, now: float):
        """Activity in the window. While the screensaver is up the page handles it itself (see `dismissed`)."""
        if not self.overlay:
            self.activity = max(self.activity, now)

    def dismissed(self, now: float):
        self.overlay = False
        self.activity = now

    def next_deadline(self) -> Optional[float]:
        deadlines = []
        if self.saver_timeout and not self.overlay and not self.saver_pending:
            deadlines.append(max(self.activity, self.saver_tried) + self.saver_timeout)
        if self.refresh_idle:
            deadlines.append(max(self.activity, self.refreshed) + self.refresh_idle)
        return min(deadlines) if deadlines else None

    def take_due(self, now: float) -> list[str]:
        """The actions to run now. A refresh counts as done; the screensaver is pending until `saver_result`."""
        due = []
        if self.saver_timeout and not self.overlay and not self.saver_pending \
                and now + _EPSILON >= max(self.activity, self.saver_tried) + self.saver_timeout:
            self.saver_pending = True
            due.append(SAVER)
        if self.refresh_idle and now + _EPSILON >= max(self.activity, self.refreshed) + self.refresh_idle:
            self.refreshed = now
            due.append(REFRESH)
        return due

    def saver_result(self, shown: bool, now: float):
        self.saver_pending = False
        self.overlay = shown
        if not shown:
            self.saver_tried = now      # nothing to play (yet) or the page did not answer: ask again in a timeout


class IdleWatch:
    """The idle logic of one window on the asyncio loop."""

    def __init__(self, show_saver: Callable[[], Awaitable[bool]], refresh: Callable[[], Awaitable[None]],
                 clock: Callable[[], float] = time.monotonic):
        self._show_saver = show_saver
        self._refresh = refresh
        self._clock = clock
        self.state = IdleState(clock())
        self._handle: Optional[asyncio.TimerHandle] = None

    def configure(self, saver_timeout: Optional[float], refresh_idle: Optional[float]):
        self.state.configure(saver_timeout, refresh_idle, self._clock())
        self._arm()

    def activity(self):
        self.state.touch(self._clock())
        # the timer is not moved: it fires at the old deadline, finds it was not reached and arms the new one

    def dismissed(self):
        self.state.dismissed(self._clock())
        self._arm()

    def reset(self):
        """The page has been (re)loaded: whatever it was showing is gone."""
        self.state.configure(self.state.saver_timeout, self.state.refresh_idle, self._clock())
        self._arm()

    def close(self):
        if self._handle:
            self._handle.cancel()
            self._handle = None

    def _arm(self):
        self.close()
        deadline = self.state.next_deadline()
        if deadline is not None:
            delay = max(0.0, deadline - self._clock())
            self._handle = asyncio.get_running_loop().call_later(delay, self._fire)

    def _fire(self):
        self._handle = None
        for action in self.state.take_due(self._clock()):
            asyncio.ensure_future(self._run(action))
        self._arm()

    async def _run(self, action: str):
        try:
            if action == SAVER:
                shown = bool(await self._show_saver())
                self.state.saver_result(shown, self._clock())
            else:
                await self._refresh()
        except Exception as exc:
            logger.warning('Idle action {} failed: {}', action, exc)
            if action == SAVER:
                self.state.saver_result(False, self._clock())
        self._arm()


def schedule_due(schedules: Iterable[str], when: datetime) -> bool:
    """Whether any of the cron-style schedules matches this minute. An invalid one never matches."""
    for expression in schedules:
        try:
            if croniter.match(expression, when):
                return True
        except (ValueError, KeyError):
            logger.warning('Invalid refresh schedule ignored: {!r}', expression)
    return False


def seconds_to_next_minute(now: datetime) -> float:
    """From `now` to the start of the next minute (plus a little, so the clock has surely turned over)."""
    return 60 - now.second - now.microsecond / 1e6 + 0.05
