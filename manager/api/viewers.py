import asyncio
from typing import Optional

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.models.playlists import playlists_manager
from utils.viewer_manager import ViewerManager


def _vm() -> ViewerManager:
    # deferred: ViewerManager is initialized in main.py, after this module
    # has already been imported and instantiated by the WebSocketAPI registry
    return ViewerManager.get_instance()


class Viewers(WSAPIBase):

    def list(self):
        return WSBroadcast(
            viewers=_vm().get_viewers(),
            playlists=playlists_manager.serialize(),
            assignments=_vm().get_assignments(),
        )

    def assign(self, viewer_id: str, playlist_id: str):
        _vm().assign(viewer_id, playlist_id)
        return self.list()

    def unassign(self, viewer_id: str):
        _vm().unassign(viewer_id)
        return self.list()

    def addWindow(self, viewer_id: str, display: int = 0,
                  x: int = 0, y: int = 0, width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    def removeWindow(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()
