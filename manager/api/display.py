from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from webview_controller.controller import Controller


class Display(WSAPIBase):
    controller = Controller.get_instance()

    def getDisplays(self):
        return WSBroadcast(displays=self.controller.GetAllDisplays())

    def getGPUFeatureStats(self):
        return WSBroadcast(features=self.controller.GetGPUFeatureStats())

    def getBounds(self):
        """
        Get viewer window bounds
        """
        if self.controller.connected:
            return WSBroadcast(**self.controller.Window[0].bounds)
        else:
            # self.controller.bounds may be None if the webview process is not running, explicitly return none in this case
            return WSBroadcast()

    def setBounds(self, x: int, y: int, width: int, height: int):
        """
        Set viewer window bounds
        """
        self.controller.Window[0].bounds = {'x': x, 'y': y, 'width': width, 'height': height}
        return self.getBounds()

    def getOrientation(self):
        return WSBroadcast(orientation=self.controller.Window[0].orientation)

    def setOrientation(self, orientation: int):
        self.controller.Window[0].orientation = orientation
        return self.getOrientation()

    def getFlip(self):
        return WSBroadcast(flip=self.controller.Window[0].flip)

    def setFlip(self, flip: int):
        self.controller.Window[0].flip = flip
        return self.getFlip()
