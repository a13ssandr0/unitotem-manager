import asyncio

from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from unitotem_node.system import firmware

POLL_INTERVAL = 2  # seconds, only while an operation is running


class Firmware(WSAPIBase):
    _poller: asyncio.Task | None = None

    async def _poll(self):
        """Pushes the status to every client while the helper reports an operation in progress."""
        while True:
            status = await firmware.status()
            await self.ws.broadcast('Settings/Firmware/status', status=status)
            if not status.get('op'):
                return
            await asyncio.sleep(POLL_INTERVAL)

    def _start_polling(self):
        if Firmware._poller is None or Firmware._poller.done():
            Firmware._poller = asyncio.get_running_loop().create_task(self._poll(), name='firmware_poll')

    async def status(self):
        status = await firmware.status()
        if status.get('op'):
            self._start_polling()
        return WSBroadcast(status=status)

    async def check(self):
        result = await firmware.check()
        await self.ws.broadcast('Settings/Firmware/status', status=await firmware.status())
        return WSBroadcast(check=result)

    async def apply(self, files: bool = True, eeprom: bool = True):
        result = await firmware.apply(files, eeprom)
        self._start_polling()
        return WSBroadcast(result=result)
