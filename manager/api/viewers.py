import asyncio
from typing import Optional

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.models.playlists import playlists_manager
from webview.controller import WebviewManager


def _vm() -> WebviewManager:
    # deferred: WebviewManager is initialized in main.py, after this module
    # has already been imported and instantiated by the WebSocketAPI registry
    return WebviewManager.get_instance()


class Viewers(WSAPIBase):

    def list(self):
        return WSBroadcast(
            viewers=_vm().get_webviews(),
            playlists=playlists_manager.serialize(),
            assignments=_vm().get_assignments(),
        )

    def assign(self, viewer_id: str, window_id: int, playlist_id: str):
        """Put one window on one playlist. A window shows a single playlist,
        while a playlist may drive as many windows as wanted."""
        _vm().assign(viewer_id, window_id, playlist_id)
        return self.list()

    def unassign(self, viewer_id: str, window_id: int):
        _vm().unassign(viewer_id, window_id)
        return self.list()

    def addWindow(self, viewer_id: str, display: int = 0,
                  x: int = 0, y: int = 0, width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    def removeWindow(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()
