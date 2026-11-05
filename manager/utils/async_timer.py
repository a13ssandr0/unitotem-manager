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
        self._task = asyncio.ensure_future(self._job())

    async def _job(self):
        if self._timeout is None or not self._callback:
            return
        await asyncio.sleep(self._timeout)
        c = self._callback()
        if isawaitable(c):
            await c

    def cancel(self):
        self._task.cancel()
    
    def reset(self):
        self.cancel()
        self._task = asyncio.ensure_future(self._job())

    def set_timeout(self, timeout):
        if isinstance(timeout, datetime):
            timeout = (timeout-datetime.now()).total_seconds()
        elif isinstance(timeout, timedelta):
            timeout = timeout.total_seconds()
            
        self._timeout = timeout
        self.reset()