from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from webview_controller.controller import controller


class Display(WSAPIBase):
    @staticmethod
    def getBounds():
        """
        Get viewer window bounds
        """
        try:
            return WSBroadcast(**controller.bounds)
        except TypeError:
            pass

    def setBounds(self, x: int, y: int, width: int, height: int):
        """
        Set viewer window bounds
        """
        controller.bounds = {'x': x, 'y': y, 'width': width, 'height': height}
        return self.getBounds()

    @staticmethod
    def getOrientation():
        return WSBroadcast(orientation=controller.orientation)

    def setOrientation(self, orientation: int):
        controller.orientation = orientation
        return self.getOrientation()

    @staticmethod
    def getFlip():
        return WSBroadcast(flip=controller.flip)

    def setFlip(self, flip: int):
        controller.flip = flip
        return self.getFlip()
