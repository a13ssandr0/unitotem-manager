import asyncio
from typing import Optional

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSResponse

from utils.models.playlists import playlists_manager
import utils.plugins as plugins
from utils.models.viewer import KeyboardSettings
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

    def assign_playlist(self, viewer_id: str, window_id: int, playlist_id: str):
        """Put one window on one playlist. A window shows a single playlist,
        while a playlist may drive as many windows as wanted."""
        _vm().assign(viewer_id, window_id, playlist_id)
        return self.list()

    def unassign_playlist(self, viewer_id: str, window_id: int):
        _vm().unassign(viewer_id, window_id)
        return self.list()

    def add_window(self, viewer_id: str, display: int = 0,
                  x: int = 0, y: int = 0, width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    def remove_window(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()

    def set_audio_output(self, viewer_id: str, window_id: int, device: Optional[str] = None):
        """Where a window plays its sound: the description of one of the outputs of its host, None for the default."""
        async def apply():
            try:
                await _vm().set_audio_output(viewer_id, window_id, device)
            except KeyError:
                pass
        asyncio.create_task(apply())
        return None

    def keyboard(self, viewer_id: str, window_id: int):
        """The on-screen keyboard of a window."""
        try:
            settings = _vm().get_keyboard(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(error=str(e))
        return WSBroadcast(viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    def set_keyboard(self, viewer_id: str, window_id: int, enabled: bool,
                     button_position: str = 'bottom-right', layout: str = 'english'):
        """Turns the on-screen keyboard of a window on or off, and says where its button is and which layout it has."""
        async def apply():
            try:
                await _vm().set_keyboard(viewer_id, window_id, KeyboardSettings(
                    enabled=enabled, button_position=button_position, layout=layout))
            except KeyError:
                pass
        asyncio.create_task(apply())
        return WSBroadcast(self.keyboard, viewer_id=viewer_id, window_id=window_id, enabled=enabled,
                           button_position=button_position, layout=layout)

    def audio_outputs(self, viewer_id: str):
        """The outputs a window of this viewer can choose from. Only this node's own sound devices are known: a
        remote viewer reports none yet, so its windows can only use the default."""
        if viewer_id != 'local-webview':
            return WSBroadcast(viewer_id=viewer_id, outputs=[])
        outputs = []
        for _plugin, hook in plugins.each('audio_outputs'):
            outputs = hook()
        return WSBroadcast(viewer_id=viewer_id, outputs=outputs)
