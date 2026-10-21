"""
WebviewWindow: one display window, a frameless fullscreen window of CEF's own windowing (the Views API of cefpython) on a Wayland
compositor, showing the boot page.

CEF cannot be embedded in a Qt window there (the protocol has no foreign windows), so the browser window is a window of its own
and there is no Qt window at all. The window asks the compositor for fullscreen on a given output itself; Qt is only used to
learn about the outputs and to draw the splash.
"""
import json
import logging
from typing import Optional
from pathlib import Path

from PySide6.QtGui import QScreen

try:
    from cefpython3 import cefpython as cef
except ImportError:
    cef = None  # guarded at WebviewApp init time

# container index → name, must match show() in boot-screen.html
_CONTAINERS = ['boot', 'web', 'image', 'video', 'audio']
# FitEnum value → CSS object-fit name
_FIT_NAMES  = ['contain', 'cover', 'fill']

logger = logging.getLogger(__name__)

APP_ID = 'org.unitotem.viewer'       # xdg_toplevel.app_id of every window: what the compositor's rules can match

_BOOT_URL = (Path(__file__).parent / 'static' / 'boot-screen.html').resolve().as_uri()


class PageCommands:
    """What the manager asks of a window, done by calling the functions of boot-screen.html. Shared by the windows of both
    graphics stacks: they only have to provide `self._cef.execute_js`."""

    def show_asset(self, src: str, container: int, fit: int, bg_color: str,
                   volume: float = 1.0, muted: bool = False, zoom: float = 1.0,
                   allow_pinch_zoom: bool = False, idle: bool = False):
        """
        Display an asset.

        container: index into ['boot','web','image','video','audio'] (= media_type + 1)
        fit:       FitEnum int value → 'contain' | 'cover' | 'fill'
        bg_color:  CSS colour string e.g. 'rgb(0,0,0)'
        idle:      there is nothing to play (the idle page): a screensaver has nothing to show
        """
        # A negative index means the media type is unknown: pass null so the
        # page probes the URL and decides for itself (see show() in
        # boot-screen.html). Falling back to 'boot' here instead, as this used
        # to, made such an asset invisible - the logo just stayed up.
        # Whatever arrives (an older or remote manager, a hand-edited file) is clamped here, the page divides by it
        try:
            zoom = min(5.0, max(0.25, float(zoom)))
        except (TypeError, ValueError):
            zoom = 1.0
        cont = _CONTAINERS[container] if 0 <= container < len(_CONTAINERS) else None
        fit_s = _FIT_NAMES[fit] if 0 <= fit < len(_FIT_NAMES) else 'contain'
        self._cef.execute_js(
            f'show({json.dumps(src)}, {json.dumps(cont)}, {json.dumps(fit_s)}, {json.dumps(bg_color)}, '
            f'{json.dumps(volume)}, {json.dumps(muted)}, {json.dumps(zoom)}, {json.dumps(bool(allow_pinch_zoom))}, '
            f'{json.dumps(bool(idle))})'
        )

    def set_audio_output(self, device: Optional[str]):
        """Send this window's sound to the output with that name (its description, as the browser lists it);
        None means the default one, i.e. whatever the system decides."""
        self._cef.execute_js(f'setAudioOutput({json.dumps(device)})')

    def set_orientation(self, orientation: int):
        # orientation is a quarter-turn index (0/1/2/3 = 0/90/180/270deg),
        # not a raw degree value. boot-screen.html's setOrientation() applies
        # it via a body[r] CSS attribute selector with the correct swapped
        # width/height + transform-origin per rotation (a plain
        # transform:rotate() would clip the content at 90/270deg, since the
        # unrotated box still occupies the original, now-perpendicular,
        # viewport dimensions).
        self._cef.execute_js(f'setOrientation({orientation})')

    def set_flip(self, flip: int):
        # flip: 0=none, 1=horizontal, 2=vertical (mutually exclusive, no
        # "both" state). Applied to a separate wrapper element from
        # orientation (see boot-screen.html's setFlip()) so the two compose
        # independently regardless of order - entangling them into a single
        # element's transform meant setting orientation again erased any
        # flip, and flip mirrored along the rotated axes instead of the
        # screen's true horizontal/vertical.
        self._cef.execute_js(f'setFlip({flip})')


class _BrowserViewDelegate:
    """Callbacks of the browser view of a window (cefpython Views API)."""

    def __init__(self, owner: 'WebviewWindow'):
        self._owner = owner

    def OnBrowserCreated(self, browser_view, browser):
        self._owner._browser = browser

    def OnBrowserDestroyed(self, browser_view, browser):
        self._owner._browser = None


class _WindowDelegate:
    """Callbacks of the window itself: frameless, fullscreen on its output, the application id of the compositor's rules."""

    def __init__(self, owner: 'WebviewWindow'):
        self._owner = owner

    def OnWindowCreated(self, window):
        owner = self._owner
        window.SetToFillLayout()
        window.AddChildView(owner._view)            # the browser is created now
        display = cef.Display.FindByOutputName(owner.screen.name())      # ids change at every plug: look it up now
        if display is not None:
            window.SetFullscreenOnDisplay(display.GetID())
        else:
            logger.warning('Window %s: the browser does not know the output %s, fullscreen where the compositor puts it',
                           owner.window_id, owner.screen.name())
            window.SetFullscreen(True)
        window.Show()

    def OnWindowDestroyed(self, window):
        self._owner._window = None

    def IsFrameless(self, window):
        return True

    def CanClose(self, window):
        return True

    def GetLinuxWindowProperties(self, window):
        return {'wayland_app_id': APP_ID}


class WebviewWindow(PageCommands):
    """One display window: a frameless fullscreen window of CEF's own windowing (Views) on the output of its screen, showing the
    boot page. There is no Qt window; Qt only tells which outputs exist (app.py)."""

    def __init__(self, window_id: int, screen: QScreen, x: int = 0, y: int = 0, width: int = 0, height: int = 0):
        self._window_id = window_id
        self.screen = screen
        self._cef = self                    # what PageCommands talks to
        self._view = None
        self._window = None
        self._browser = None
        self.on_geometry_changed = None     # set by the application

    @property
    def window_id(self) -> int:
        return self._window_id

    def geometry(self):
        return self.screen.geometry()

    def show(self):
        """Creates the window and its browser, on the output of the screen. The address carries `?window=N`: it is how the
        DevTools connection (cdp.py) knows which window a page belongs to."""
        if self._window is not None:
            return
        self._view = cef.BrowserView.CreateBrowserView(
            url=f'{_BOOT_URL}?window={self._window_id}', settings={'background_color': 0xFF000000},
            delegate=_BrowserViewDelegate(self))
        self._window = cef.Window.CreateTopLevelWindow(_WindowDelegate(self))

    def execute_js(self, js: str):
        if self._browser is not None:
            self._browser.ExecuteJavascript(js)

    def close(self):
        """Destroys the window for real (the toplevel goes away, the browser is closed)."""
        window, self._window = self._window, None
        if window is not None:
            window.Close()
