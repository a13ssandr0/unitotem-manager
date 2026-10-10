import asyncio

from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.system import updater

POLL_INTERVAL = 2  # seconds, only while an operation is running


class Update(WSAPIBase):
    _poller: asyncio.Task | None = None

    async def _poll(self):
        """Pushes the status to every client while the updater reports an operation in progress."""
        while True:
            status = await updater.status()
            await self.ws.broadcast('Settings/Update/status', status=status)
            if not status.get('op'):
                return
            await asyncio.sleep(POLL_INTERVAL)

    def _start_polling(self):
        if Update._poller is None or Update._poller.done():
            Update._poller = asyncio.get_running_loop().create_task(self._poll(), name='update_poll')

    async def status(self):
        status = await updater.status()
        if status.get('op'):
            self._start_polling()
        return WSBroadcast(status=status)

    @staticmethod
    async def check():
        return WSBroadcast(check=await updater.check())

    async def fetch(self, force: bool = False):
        result = await updater.fetch(force)
        self._start_polling()
        return WSBroadcast(result=result)

    async def cancel(self):
        result = await updater.cancel()
        self._start_polling()
        return WSBroadcast(result=result)

    async def apply(self, reboot: bool = True):
        result = await updater.apply(reboot)
        self._start_polling()
        return WSBroadcast(result=result)

    async def confirm(self):
        result = await updater.confirm()
        self._start_polling()
        return WSBroadcast(result=result)

    async def rollback(self, reboot: bool = True):
        result = await updater.rollback(reboot)
        self._start_polling()
        return WSBroadcast(result=result)
