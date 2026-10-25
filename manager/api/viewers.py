import asyncio
from typing import List, Optional

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSResponse

from utils.models.playlists import playlists_manager
import utils.plugins as plugins
from utils.models.user import UserPerms
from pydantic import ValidationError

from utils.models.layout import Area, LayoutError, normalize_color
from utils.models.viewer import KeyboardSettings, RefreshSettings, ScreensaverSettings
from webview.controller import WebviewManager


def _vm() -> WebviewManager:
    # deferred: WebviewManager is initialized in main.py, after this module
    # has already been imported and instantiated by the WebSocketAPI registry
    return WebviewManager.get_instance()


def _first_error(error: ValidationError) -> str:
    return error.errors()[0]['msg'].removeprefix('Value error, ')


class Viewers(WSAPIBase):
    # note: a method called `list` shadows the builtin in annotations here, hence typing.List

    @UserPerms.requires.scheduler
    def list(self):
        return WSBroadcast(
            viewers=_vm().get_webviews(),
            playlists=playlists_manager.serialize(),
            assignments=_vm().get_assignments(),
        )

    @UserPerms.requires.scheduler
    def assign_playlist(self, viewer_id: str, window_id: int, playlist_id: str, pane_id: Optional[int] = None):
        """Put one pane of a window on one playlist (without pane_id, the main pane: a window that is not divided has only
        that one). A pane shows a single playlist, while a playlist may drive as many panes and windows as wanted."""
        try:
            _vm().assign(viewer_id, window_id, playlist_id, pane_id)
        except LayoutError as e:
            return WSResponse(error=str(e))
        return self.list()

    @UserPerms.requires.scheduler
    def unassign_playlist(self, viewer_id: str, window_id: int, pane_id: Optional[int] = None):
        try:
            _vm().unassign(viewer_id, window_id, pane_id)
        except LayoutError as e:
            return WSResponse(error=str(e))
        return self.list()

    @UserPerms.requires.admin
    def add_window(self, viewer_id: str, display: int = 0,
                  x: int = 0, y: int = 0, width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    @UserPerms.requires.admin
    def remove_window(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()

    @UserPerms.requires.scheduler
    def layout(self, viewer_id: str, window_id: int):
        """The layout of a window: the rectangle its content is drawn in, and its panes (a tree of splits) with their playlists."""
        return self._layout_message(viewer_id, window_id)

    def _layout_message(self, viewer_id: str, window_id: int):
        try:
            _vm().get_layout(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(self.layout, error=str(e))
        return WSBroadcast(self.layout, viewer_id=viewer_id, window_id=window_id,
                           layout=_vm().layout_info(viewer_id, window_id))

    @UserPerms.requires.admin
    def set_area(self, viewer_id: str, window_id: int, left: float = 0, top: float = 0, width: float = 100,
                 height: float = 100, margin_color: str = 'rgb(0,0,0)'):
        """The rectangle of the window the content is drawn in, in percent of the window (after its orientation); the rest
        is blank, margin_color. The values are clamped: at least 5 % each way, inside the window."""
        if normalize_color(margin_color) is None:
            return WSResponse(self.layout, error='The margin colour must be a plain colour (#rrggbb, rgb(r,g,b) or rgba(r,g,b,a))')
        try:
            _vm().set_area(viewer_id, window_id, Area(left=left, top=top, width=width, height=height,
                                                      margin_color=margin_color))
        except (KeyError, LayoutError) as e:
            return WSResponse(self.layout, error=str(e))
        return self._layout_message(viewer_id, window_id)

    @UserPerms.requires.admin
    def split_pane(self, viewer_id: str, window_id: int, pane_id: int, direction: str):
        """Divides a pane in two: direction 'h' puts the new pane at its right, 'v' below it. At most 8 panes, 4 levels deep."""
        try:
            _vm().split_pane(viewer_id, window_id, pane_id, direction)
        except (KeyError, LayoutError) as e:
            return WSResponse(self.layout, error=str(e))
        return self._layout_message(viewer_id, window_id)

    @UserPerms.requires.admin
    def close_pane(self, viewer_id: str, window_id: int, pane_id: int):
        """Closes a pane: its neighbour takes the space. The last pane of a window cannot be closed."""
        try:
            _vm().close_pane(viewer_id, window_id, pane_id)
        except (KeyError, LayoutError) as e:
            return WSResponse(self.layout, error=str(e))
        return self.list()      # closing the main pane changes the playlist of the window as well

    @UserPerms.requires.admin
    def set_split_ratio(self, viewer_id: str, window_id: int, path: str, ratio: float):
        """Moves the splitter of one split: `path` leads to it from the root ('' is the root split, 'a' and 'b' its
        first and second half, 'ab' the second half of the first...), `ratio` is the share of the first half (10 %..90 %)."""
        try:
            _vm().set_split_ratio(viewer_id, window_id, path, ratio)
        except (KeyError, LayoutError) as e:
            return WSResponse(self.layout, error=str(e))
        return self._layout_message(viewer_id, window_id)

    @UserPerms.requires.scheduler
    def set_audio_output(self, viewer_id: str, window_id: int, device: Optional[str] = None):
        """Where a window plays its sound: the description of one of the outputs of its host, None for the default."""
        async def apply():
            try:
                await _vm().set_audio_output(viewer_id, window_id, device)
            except KeyError:
                pass
        asyncio.create_task(apply())
        return None

    @UserPerms.requires.admin
    def keyboard(self, viewer_id: str, window_id: int):
        """The on-screen keyboard of a window."""
        try:
            settings = _vm().get_keyboard(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(error=str(e))
        return WSBroadcast(viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    @UserPerms.requires.admin
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

    @UserPerms.requires.scheduler
    def screensaver(self, viewer_id: str, window_id: int):
        """The interactive mode of a window: its home page and the idle time after which the playlist is played over it."""
        try:
            settings = _vm().get_screensaver(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(error=str(e))
        return WSBroadcast(viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    @UserPerms.requires.scheduler
    def set_screensaver(self, viewer_id: str, window_id: int, enabled: bool,
                        home_url: str = '', idle_timeout: int = 120):
        """Makes a window interactive: home page (http or https) shown indefinitely, playlist as a screensaver after
        idle_timeout seconds (5 s to 24 h) without a touch or a key; the first touch ends it and is not passed on."""
        try:
            settings = ScreensaverSettings(enabled=enabled, home_url=home_url, idle_timeout=idle_timeout)
        except ValidationError as e:
            return WSResponse(error=_first_error(e))

        async def apply():
            try:
                await _vm().set_screensaver(viewer_id, window_id, settings)
            except KeyError:
                pass
        asyncio.create_task(apply())
        return WSBroadcast(self.screensaver, viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    @UserPerms.requires.scheduler
    def refresh(self, viewer_id: str, window_id: int):
        """When the web page of a window is reloaded (a window without a screensaver)."""
        try:
            settings = _vm().get_refresh(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(error=str(e))
        return WSBroadcast(viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    @UserPerms.requires.scheduler
    def set_refresh(self, viewer_id: str, window_id: int, refresh_after_idle: Optional[int] = None,
                    refresh_schedules: Optional[List[str]] = None):
        """When a window reloads its web page: after refresh_after_idle seconds without activity (10 s to 7 days, empty
        for never) and/or at the minutes matching any of refresh_schedules (five-field cron expressions)."""
        try:
            settings = RefreshSettings(refresh_after_idle=refresh_after_idle, refresh_schedules=refresh_schedules or [])
        except ValidationError as e:
            return WSResponse(error=_first_error(e))

        async def apply():
            try:
                await _vm().set_refresh(viewer_id, window_id, settings)
            except KeyError:
                pass
        asyncio.create_task(apply())
        return WSBroadcast(self.refresh, viewer_id=viewer_id, window_id=window_id, **settings.model_dump())

    @UserPerms.requires.scheduler
    def audio_outputs(self, viewer_id: str):
        """The outputs a window of this viewer can choose from. Only this node's own sound devices are known: a
        remote viewer reports none yet, so its windows can only use the default."""
        if viewer_id != 'local-webview':
            return WSBroadcast(viewer_id=viewer_id, outputs=[])
        outputs = []
        for _plugin, hook in plugins.each('audio_outputs'):
            outputs = hook()
        return WSBroadcast(viewer_id=viewer_id, outputs=outputs)
