from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.system import updater


class Update(WSAPIBase):
    @staticmethod
    async def status():
        return WSBroadcast(status=await updater.status())

    @staticmethod
    async def check():
        return WSBroadcast(check=await updater.check())
