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

from PySide6.QtCore import QTimer
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

DISPLAY_WAIT_MS, DISPLAY_WAIT_TRIES = 150, 40     # up to 6 s for the browser to know a new output
APP_ID = 'org.unitotem.viewer'       # xdg_toplevel.app_id of every window: what the compositor's rules can match

_BOOT_URL = (Path(__file__).parent / 'static' / 'boot-screen.html').resolve().as_uri()


class PageCommands:
    """What the manager asks of a window, done by calling the functions of boot-screen.html. Shared by the windows of both
    graphics stacks: they only have to provide `self._cef.execute_js`."""

    def show_asset(self, src: str, container: int, fit: int, bg_color: str,
                   volume: float = 1.0, muted: bool = False, zoom: float = 1.0,
                   allow_pinch_zoom: bool = False, idle: bool = False, pane: Optional[int] = None):
        """
        Display an asset in a pane of the window (None: the main pane).

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
            f'{json.dumps(bool(idle))}, {json.dumps(pane)})'
        )

    def set_asset_audio(self, volume: float, muted: bool, pane: Optional[int] = None):
        """Change volume/mute of the asset being shown in a pane (None: the main one), without showing it again."""
        self._cef.execute_js(f'setAssetAudio({json.dumps(volume)}, {json.dumps(bool(muted))}, {json.dumps(pane)})')

    def set_window_layout(self, area: dict, tree: dict):
        """The rectangle the content is drawn in and the tree of panes (see utils/models/layout.py): the page builds one stage
        per pane, keeps the ones that survive playing and drops the others. The page sanitises what it is given."""
        self._cef.execute_js(f'setWindowLayout({json.dumps(area)}, {json.dumps(tree)})')

    def set_audio_output(self, device: Optional[str]):
        """Send this window's sound to the output with that name (its description, as the browser lists it);
        None means the default one, i.e. whatever the system decides."""
        self._cef.execute_js(f'setAudioOutput({json.dumps(device)})')

    orientation = 0     # what the page has been told: reported to the manager (WebviewInfo)
    flip = 0

    def set_orientation(self, orientation: int):
        self.orientation = int(orientation) % 4
        # orientation is a quarter-turn index (0/1/2/3 = 0/90/180/270deg),
        # not a raw degree value. boot-screen.html's setOrientation() applies
        # it via a body[r] CSS attribute selector with the correct swapped
        # width/height + transform-origin per rotation (a plain
        # transform:rotate() would clip the content at 90/270deg, since the
        # unrotated box still occupies the original, now-perpendicular,
        # viewport dimensions).
        self._cef.execute_js(f'setOrientation({orientation})')

    def set_flip(self, flip: int):
        self.flip = flip if flip in (1, 2) else 0
        # flip: 0=none, 1=horizontal, 2=vertical (mutually exclusive, no
        # "both" state). Applied to a separate wrapper element from
        # orientation (see boot-screen.html's setFlip()) so the two compose
        # independently regardless of order - entangling them into a single
        # element's transform meant setting orientation again erased any
        # flip, and flip mirrored along the rotated axes instead of the
        # screen's true horizontal/vertical.
        self._cef.execute_js(f'setFlip({flip})')


class _DisplayWatch:
    """Windows waiting for the browser to know their output. A new output reaches Qt first and the browser a moment later; the
    browser says when (cef.Display.AddObserver, a CEF with that API), and without that API the window polls (WebviewWindow.show)."""

    waiting: set = set()
    registered = False

    @classmethod
    def available(cls) -> bool:
        return cef is not None and hasattr(cef.Display, 'AddObserver')

    @classmethod
    def ensure(cls):
        if not cls.registered and cls.available():
            cef.Display.AddObserver(cls)
            cls.registered = True

    @classmethod
    def OnDisplayAdded(cls, display):
        for window in list(cls.waiting):
            window.show()

    @classmethod
    def OnDisplayRemoved(cls, display):
        pass

    @classmethod
    def OnDisplayMetricsChanged(cls, display):
        pass


class _BrowserViewDelegate:
    """Callbacks of the browser view of a window (cefpython Views API)."""

    def __init__(self, owner: 'WebviewWindow'):
        self._owner = owner

    def OnBrowserCreated(self, browser_view, browser):
        self._owner._browser = browser
        # The browser exists before its page does: a command run now would reach the blank initial document, which the
        # navigation then replaces (a window restored to its playlist at start showed nothing). They wait for the load.
        browser.SetClientCallback('OnLoadEnd', self._on_load_end)

    def _on_load_end(self, browser, frame, http_code):
        if frame.IsMain():
            self._owner._page_loaded = True
            for js in self._owner._pending_js:      # commands that arrived before the page was there
                browser.ExecuteJavascript(js)
            self._owner._pending_js.clear()

    def OnBrowserDestroyed(self, browser_view, browser):
        self._owner._browser = None
        self._owner._page_loaded = False


class _WindowDelegate:
    """Callbacks of the window itself: frameless, fullscreen on its output, the application id of the compositor's rules."""

    def __init__(self, owner: 'WebviewWindow'):
        self._owner = owner

    def OnWindowCreated(self, window):
        owner = self._owner
        window.SetToFillLayout()
        window.AddChildView(owner._view)            # the browser is created now
        if owner._display_id is not None:
            window.SetFullscreenOnDisplay(owner._display_id)
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
        self._page_loaded = False           # boot-screen.html is there: its functions can be called
        self._display_id = None
        self._pending_js: list[str] = []
        self.closed = False
        self._attempts = 0
        self.on_geometry_changed = None     # set by the application

    @property
    def window_id(self) -> int:
        return self._window_id

    def geometry(self):
        return self.screen.geometry()

    def show(self):
        """Creates the window and its browser, on the output of the screen. The address carries `?window=N`: it is how the
        DevTools connection (cdp.py) knows which window a page belongs to."""
        if self._window is not None or self.closed:
            return
        # The browser learns about a new output a moment after Qt does, and the id changes at every plug: look it up now, and wait
        # for it (a window made too early would go fullscreen on whatever output the compositor likes)
        display = cef.Display.FindByOutputName(self.screen.name())
        if display is None and _DisplayWatch.available() and self._attempts < DISPLAY_WAIT_TRIES:
            _DisplayWatch.ensure()
            if self not in _DisplayWatch.waiting:
                _DisplayWatch.waiting.add(self)
                QTimer.singleShot(DISPLAY_WAIT_MS * DISPLAY_WAIT_TRIES, self._give_up_waiting)      # the observer may never come
            return
        if display is None and self._attempts < DISPLAY_WAIT_TRIES:
            self._attempts += 1
            QTimer.singleShot(DISPLAY_WAIT_MS, self.show)
            return
        _DisplayWatch.waiting.discard(self)
        self._display_id = display.GetID() if display is not None else None
        self._view = cef.BrowserView.CreateBrowserView(
            url=f'{_BOOT_URL}?window={self._window_id}', settings={'background_color': 0xFF000000},
            delegate=_BrowserViewDelegate(self))
        self._window = cef.Window.CreateTopLevelWindow(_WindowDelegate(self))

    def _give_up_waiting(self):
        if self in _DisplayWatch.waiting and self._window is None:
            self._attempts = DISPLAY_WAIT_TRIES
            self.show()

    def execute_js(self, js: str):
        if self._browser is not None and self._page_loaded:
            self._browser.ExecuteJavascript(js)
        elif not self.closed:
            self._pending_js.append(js)

    def close(self):
        """Destroys the window for real (the toplevel goes away, the browser is closed)."""
        self.closed = True
        _DisplayWatch.waiting.discard(self)
        self._pending_js.clear()
        window, self._window = self._window, None
        if window is not None:
            window.Close()
