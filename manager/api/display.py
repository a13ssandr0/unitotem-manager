import asyncio
from typing import Optional

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.viewer_manager import ViewerManager


def _vm() -> ViewerManager:
    # deferred: ViewerManager is initialized in main.py, after this module
    # has already been imported and instantiated by the WebSocketAPI registry
    return ViewerManager.get_instance()


class Display(WSAPIBase):

    def getDisplays(self, viewer_id: Optional[str] = None):
        if viewer_id:
            return WSBroadcast(displays=_vm().get_viewer_screens(viewer_id))
        # Return screens grouped by viewer
        return WSBroadcast(displays={
            vid: _vm().get_viewer_screens(vid)
            for vid in _vm().get_viewers()
        })

    def getGPUFeatureStats(self):
        return WSBroadcast(features={})

    def getBounds(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_viewer_windows(viewer_id)
        if window_id < len(windows):
            return WSBroadcast(**windows[window_id])
        return WSBroadcast()

    def setBounds(self, viewer_id: str, window_id: int, x: int, y: int, width: int, height: int):
        asyncio.create_task(_vm().set_bounds(viewer_id, window_id, x, y, width, height))
        return WSBroadcast(x=x, y=y, width=width, height=height)

    def getOrientation(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_viewer_windows(viewer_id)
        orientation = windows[window_id].get('orientation', 0) if window_id < len(windows) else 0
        return WSBroadcast(orientation=orientation)

    def setOrientation(self, viewer_id: str, window_id: int, orientation: int):
        asyncio.create_task(_vm().set_orientation(viewer_id, window_id, orientation))
        return WSBroadcast(orientation=orientation)

    def getFlip(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_viewer_windows(viewer_id)
        flip = windows[window_id].get('flip', 0) if window_id < len(windows) else 0
        return WSBroadcast(flip=flip)

    def setFlip(self, viewer_id: str, window_id: int, flip: int):
        asyncio.create_task(_vm().set_flip(viewer_id, window_id, flip))
        return WSBroadcast(flip=flip)

    def addWindow(self, viewer_id: str, display: int = 0, x: int = 0, y: int = 0,
                  width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    def removeWindow(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()
