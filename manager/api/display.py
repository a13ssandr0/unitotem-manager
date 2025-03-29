from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase

DISPLAYS: list[dict] = []
WINDOW = {'bounds': {}, 'orientation': -2, 'flip': -2}


class Display(WSAPIBase):
    @staticmethod
    def getBounds():
        """
        Get viewer window bounds
        """
        return WSBroadcast(**WINDOW['bounds'])

    async def setBounds(self, x: int, y: int, width: int, height: int):
        """
        Set viewer window bounds
        """
        await self.ui_ws.broadcast('setBounds', x=x, y=y, width=width, height=height)

    @staticmethod
    def getOrientation():
        return WSBroadcast(orientation=WINDOW['orientation'])

    async def setOrientation(self, orientation: int):
        await self.ui_ws.broadcast('setOrientation', orientation=orientation)

    @staticmethod
    def getFlip():
        return WSBroadcast(flip=WINDOW['flip'])

    async def setFlip(self, flip: int):
        await self.ui_ws.broadcast('setFlip', flip=flip)
