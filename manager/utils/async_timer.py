# Adapted from a Stack Overflow answer by Mikhail Gerasimov (CC BY-SA 3.0): https://stackoverflow.com/a/45430833


import asyncio
from datetime import datetime, timedelta
from inspect import isawaitable


class Timer:
    def __init__(self, timeout, callback):
        if isinstance(timeout, datetime):
            timeout = (timeout-datetime.now()).total_seconds()
        elif isinstance(timeout, timedelta):
            timeout = timeout.total_seconds()

        self._timeout = timeout
        self._callback = callback
        self._task = None
        self._schedule()

    def _schedule(self):
        # A task only when there is something to wait for: a task that never runs is a coroutine never awaited
        if self._timeout is not None and self._callback:
            self._task = asyncio.ensure_future(self._job())
        else:
            self._task = None

    async def _job(self):
        if self._timeout is None or not self._callback:
            return
        await asyncio.sleep(self._timeout)
        c = self._callback()
        if isawaitable(c):
            await c

    def cancel(self):
        if self._task is not None:
            self._task.cancel()

    def reset(self):
        self.cancel()
        self._schedule()

    def set_timeout(self, timeout):
        if isinstance(timeout, datetime):
            timeout = (timeout-datetime.now()).total_seconds()
        elif isinstance(timeout, timedelta):
            timeout = timeout.total_seconds()
            
        self._timeout = timeout
        self.reset()