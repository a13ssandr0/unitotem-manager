"""
The scheduler of the feeds: ONE asyncio task, driven by events, no polling.

It sleeps until the earliest `next_due` of an enabled source (or until somebody pokes it: a source was added, edited,
enabled, a "fetch now" was asked, the node was told to stop), fetches what is due (a few at a time, each source in
isolation: a failure of one is recorded and never reaches the others), and tells its listeners only what changed:
`on_items(merged)` when the merged list is different, `on_status()` when a source's state is. A source that keeps
failing raises ONE notification for the administrators (after two failures in a row: a blip is not news) which is
resolved by the first success.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from typing import Awaitable, Callable, Optional, Union

from loguru import logger

from utils.models.ticker import TickerSource
from utils.notifications import notifications
from utils.ticker import fetcher
from utils.ticker.store import TickerStore

CONCURRENCY = 4
FAILS_BEFORE_NOTIFYING = 2
RETRY_BASE = 60                 # seconds; doubles after every failure, never more than the source's own interval
SAFETY_WAKEUP = 3600            # a guard against a wall clock that jumped back, not a poll

Listener = Callable[..., Union[None, Awaitable[None]]]


def notification_key(source_id: str) -> str:
    return f'ticker.source.{source_id}'


class TickerService:
    def __init__(self, store: TickerStore, fetch=fetcher.fetch, clock: Callable[[], float] = time.time,
                 insecure: Callable[[], bool] = lambda: False):
        self.store = store
        self._fetch = fetch
        self._clock = clock
        self._insecure = insecure
        self._wake = asyncio.Event()
        self._stop = False
        self._digest = ''
        self.on_items: Optional[Listener] = None
        self.on_status: Optional[Listener] = None

    # ── asked from outside (any of them from the loop's thread) ──────────

    def poke(self):
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def source_added(self, source: TickerSource):
        self.poke()

    def source_changed(self, source_id: str, refetch: bool):
        """An edit: a changed address or a source just enabled is fetched now, a changed interval reschedules."""
        st = self.store.state.get(source_id)
        source = self.store.get(source_id)
        if st:
            if refetch:
                st.next_due = 0
            elif st.last_try:
                st.next_due = st.last_try + source.interval
        if not source.enabled:
            notifications.resolve(notification_key(source_id))
        self.poke()

    def source_deleted(self, source_id: str):
        notifications.resolve(notification_key(source_id))
        self.poke()

    def fetch_now(self, source_id: Optional[str] = None):
        for source in self.store.sources:
            if source_id in (None, source.id) and source.enabled:
                self.store.state[source.id].next_due = 0
        self.poke()

    # ── the loop ─────────────────────────────────────────────────────────

    async def run(self):
        logger.info('News ticker scheduler started')
        try:
            while not self._stop:
                self._wake.clear()
                now = self._clock()
                due = [s for s in self.store.sources
                       if s.enabled and self.store.state[s.id].next_due <= now]
                if due:
                    await self._fetch_all(due)
                    continue            # an edit may have come while fetching: look again before sleeping
                upcoming = [self.store.state[s.id].next_due for s in self.store.sources if s.enabled]
                timeout = min(max(min(upcoming) - self._clock(), 0.0), SAFETY_WAKEUP) if upcoming else None
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            logger.info('News ticker scheduler stopped')

    async def _fetch_all(self, due: list[TickerSource]):
        gate = asyncio.Semaphore(CONCURRENCY)
        for source in due:
            self.store.state[source.id].fetching = True
        await self._emit('on_status')

        async def one(source: TickerSource):
            async with gate:
                await self._fetch_one(source)

        # return_exceptions: whatever one source does, the others are fetched and the task goes on
        results = await asyncio.gather(*(one(s) for s in due), return_exceptions=True)
        for source, result in zip(due, results):
            if isinstance(result, BaseException):
                logger.error('Fetching the news source {} crashed: {!r}', source.id, result)
                self._failed(source, 'Unexpected error')
        self.store.save_cache()
        await self._emit('on_status')
        await self._publish_items()

    async def _fetch_one(self, source: TickerSource):
        st = self.store.state[source.id]
        try:
            result = await self._fetch(source.url, st.etag, st.last_modified, insecure=self._insecure(),
                                       now=self._clock())
        except fetcher.FetchError as exc:
            self._failed(source, str(exc))
            return
        finally:
            st.fetching = False
        if result.not_modified:
            self.store.record_not_modified(source.id)
        else:
            self.store.record_success(source.id, result.items, result.etag, result.last_modified)
        st.next_due = self._clock() + source.interval
        notifications.resolve(notification_key(source.id))

    def _failed(self, source: TickerSource, error: str):
        st = self.store.state[source.id]
        st.fetching = False
        self.store.record_failure(source.id, error)
        st.next_due = self._clock() + min(source.interval, RETRY_BASE * 2 ** (st.fails - 1))
        logger.warning('News source {} failed ({}): {}', source.label, st.fails, error)
        if st.fails >= FAILS_BEFORE_NOTIFYING:
            notifications.notify(notification_key(source.id), 'warning', f'News feed not available: {source.label}',
                                 error, source='ticker', audience='admin', link='/viewers')

    # ── telling the listeners ────────────────────────────────────────────

    async def _publish_items(self, force: bool = False):
        merged = self.store.merged()
        digest = hashlib.sha1(json.dumps(merged, sort_keys=True).encode()).hexdigest()
        if digest == self._digest and not force:
            return
        self._digest = digest
        await self._emit('on_items', merged)

    async def items_changed(self):
        """The set of enabled sources or their names changed: the merged list may be different."""
        await self._publish_items()

    async def _emit(self, name: str, *args):
        listener = getattr(self, name)
        if listener is None:
            return
        try:
            result = listener(*args)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:        # a listener must never stop the scheduler
            logger.error('News ticker listener {} failed: {!r}', name, exc)
