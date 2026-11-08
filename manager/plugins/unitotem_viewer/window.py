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
from urllib.parse import urlencode

from PySide6.QtCore import QTimer
from PySide6.QtGui import QScreen

from . import keys

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
IDENTIFY_MS = 5000                   # how long the name of a screen stays on it (Identify from the manager)
FOCUS_DELAY_MS = 300                 # the new page's widget exists a moment after its load ends
RECOVERY_BASE_MS, RECOVERY_MAX_MS = 200, 10000      # pause before reloading a page whose process died
APP_ID = 'org.unitotem.viewer'       # xdg_toplevel.app_id of every window: what the compositor's rules can match

_BOOT_URL = (Path(__file__).parent / 'static' / 'boot-screen.html').resolve().as_uri()
_INTERNAL_PAGES = 'https://localhost/unitotem-'     # the idle pages the manager serves (no-assets, first-boot)


class PageCommands:
    """What the manager asks of a window, done by calling the functions of boot-screen.html. Shared by the windows of both
    graphics stacks: they only have to provide `self._cef.execute_js`."""

    screen_label = None     # (number, name) of the window's screen when more than one screen is connected, else None
    _idle_shows: dict       # pane -> the arguments of its idle show(), to show it again when the screen label changes

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
        if idle:
            self._idle_shows[pane] = (src, container, fit, bg_color, volume, muted, zoom, allow_pinch_zoom, idle, pane)
            src = self._with_screen_label(src)
        else:
            self._idle_shows.pop(pane, None)
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

    def _with_screen_label(self, src: str) -> str:
        """The idle page's address with the screen it is shown on: the page of a one-screen node is the same as ever."""
        if self.screen_label is None or not src.startswith(_INTERNAL_PAGES):
            return src
        number, name = self.screen_label
        return f"{src}{'&' if '?' in src else '?'}{urlencode({'screen': name, 'index': number})}"

    def set_screen_label(self, number: Optional[int], name: str = ''):
        """The screen's number (1-based) and output name, or None when only one screen is connected. The idle pages showing
        now are shown again with it: they carry it in their address."""
        label = (number, name) if number else None
        if label == self.screen_label:
            return
        self.screen_label = label
        for args in list(self._idle_shows.values()):
            self.show_asset(*args[:8], idle=True, pane=args[9])

    def identify(self, name: str, caption: str):
        """Shows the name of the output and the screen's number over the whole window for IDENTIFY_MS, in the content of
        the page (it turns and mirrors with it): boot-screen.html's identify()."""
        self._cef.execute_js(f'identify({json.dumps(name)}, {json.dumps(caption)}, {IDENTIFY_MS})')

    def set_asset_audio(self, volume: float, muted: bool, pane: Optional[int] = None):
        """Change volume/mute of the asset being shown in a pane (None: the main one), without showing it again."""
        self._cef.execute_js(f'setAssetAudio({json.dumps(volume)}, {json.dumps(bool(muted))}, {json.dumps(pane)})')

    def set_window_layout(self, area: dict, tree: dict):
        """The rectangle the content is drawn in and the tree of panes (see utils/models/layout.py): the page builds one stage
        per pane, keeps the ones that survive playing and drops the others. The page sanitises what it is given."""
        self._cef.execute_js(f'setWindowLayout({json.dumps(area)}, {json.dumps(tree)})')

    def set_touch_setup(self, state: dict):
        """The guided setup of this window's touch panel (node: system/touch_setup.py): draws the target to touch, or takes the veil away."""
        self._cef.execute_js(f'touchSetup({json.dumps(state)})')

    def set_audio_output(self, device: Optional[str]):
        """Send this window's sound to the output with that name (its description, as the browser lists it);
        None means the default one, i.e. whatever the system decides."""
        self._cef.execute_js(f'setAudioOutput({json.dumps(device)})')

    orientation = 0     # the quarter turns (clockwise) and the flip asked for, reported to the manager (WebviewInfo)
    flip = 0            # 0 none, 1 horizontal, 2 vertical
    turned_by_output = False    # the compositor turns the screen: the page leaves its content alone

    def set_turn(self, orientation: Optional[int] = None, flip: Optional[int] = None):
        """Remember the turn and/or mirror asked for (flip: 0 none, 1 horizontal, 2 vertical, mutually exclusive)."""
        if orientation is not None:
            self.orientation = int(orientation) % 4
        if flip is not None:
            self.flip = flip if flip in (1, 2) else 0

    def show_turn(self):
        """Tell the page. When the compositor already turned the output the page shows its content as it is; otherwise the page
        turns it (boot-screen.html's setOrientation/setFlip: orientation is a quarter-turn index and the two are applied to
        separate wrappers, so they compose in either order)."""
        orientation, flip = (0, 0) if self.turned_by_output else (self.orientation, self.flip)
        self._cef.execute_js(f'setOrientation({orientation}); setFlip({flip})')


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
        browser.SetClientCallback('OnPreKeyEvent', self._on_pre_key_event)
        browser.SetClientCallback('OnBeforePopup', self._on_before_popup)
        browser.SetClientCallback('OnRendererProcessTerminated', self._on_renderer_terminated)

    def _on_load_end(self, browser, frame, http_code):
        if not frame.IsMain():
            return
        owner = self._owner
        if not (frame.GetUrl() or '').startswith(_BOOT_URL):
            # something navigated the window away from our page (a script, a redirect): put it back
            logger.warning('Window %s left its page for %s: loading it again', owner.window_id, frame.GetUrl()[:200])
            browser.LoadUrl(f'{_BOOT_URL}?window={owner.window_id}')
            return
        reloaded = owner._page_loaded or owner._loads > 0
        owner._loads += 1
        QTimer.singleShot(RECOVERY_MAX_MS * 3, lambda: setattr(owner, '_recoveries', 0))
        owner._page_loaded = True
        for js in owner._pending_js:      # commands that arrived before the page was there
            browser.ExecuteJavascript(js)
        owner._pending_js.clear()
        if reloaded:
            QTimer.singleShot(FOCUS_DELAY_MS, owner.refocus)     # a replaced renderer starts without the keyboard focus
        if reloaded and owner.on_page_reloaded is not None:
            owner.on_page_reloaded(owner.window_id)     # whatever the page held is gone: the manager shows it again

    def _on_pre_key_event(self, browser, event, event_handle, is_keyboard_shortcut_out):
        """The browser's own shortcuts do nothing; a reload key asks the page shown to refresh itself."""
        decision = keys.decide(event.get('modifiers', 0), event.get('windows_key_code', 0))
        if decision is keys.Decision.RELOAD and event.get('type') in (cef.KEYEVENT_RAWKEYDOWN, cef.KEYEVENT_KEYDOWN):
            self._owner.execute_js('refreshPage()')
        return decision is not keys.Decision.PASS

    def _on_before_popup(self, *args, **kwargs):
        return True         # a page never opens windows of its own

    def _on_renderer_terminated(self, browser, status):
        logger.error('The page of window %s died (%s): reloading it', self._owner.window_id, status)
        self._owner.recover()

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
        self._idle_shows: dict = {}
        self._loads = 0                     # how many times the page has finished loading: more than one is a reload
        self._recoveries = 0                # consecutive crashes without a page that stayed up
        self.closed = False
        self._attempts = 0
        self.on_geometry_changed = None     # set by the application
        self.on_page_reloaded = None        # set by the application: called with the window id when the page loads again

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

    def refocus(self):
        """Gives the keyboard focus back to the page after its renderer was replaced. Neither the view's nor the browser's own
        focus calls do it (Views believes the focus is already there): the window has to be hidden and shown again."""
        if self.closed or self._window is None or self._view is None:
            return
        self._window.Hide()
        self._window.Show()
        self._window.Activate()
        self._view.RequestFocus()

    def recover(self):
        """The page's process died: load it again, after a pause that grows while it keeps dying (a crash loop must not burn the
        CPU), and the load tells the manager to show the content again."""
        self._page_loaded = False
        delay = min(RECOVERY_BASE_MS * 2 ** self._recoveries, RECOVERY_MAX_MS)
        self._recoveries += 1
        QTimer.singleShot(delay, self._reload_after_crash)

    def _reload_after_crash(self):
        if self._browser is not None and not self.closed:
            self._browser.Reload()

    def close(self):
        """Destroys the window for real (the toplevel goes away, the browser is closed)."""
        self.closed = True
        _DisplayWatch.waiting.discard(self)
        self._pending_js.clear()
        window, self._window = self._window, None
        if window is not None:
            window.Close()
