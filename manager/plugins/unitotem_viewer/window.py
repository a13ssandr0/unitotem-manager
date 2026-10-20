"""
WebviewWindow — frameless Qt6 window with an embedded CEF browser.

CEF renders directly to an X11 child window (via SetAsChild / winId()),
so Wayland must be backed by XWayland (QT_QPA_PLATFORM=xcb).
"""
import json
import logging
from typing import Optional
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QScreen
from PySide6.QtWidgets import QMainWindow, QWidget, QSizePolicy

try:
    from cefpython3 import cefpython as cef
except ImportError:
    cef = None  # guarded at WebviewApp init time

# container index → name, must match show() in boot-screen.html
_CONTAINERS = ['boot', 'web', 'image', 'video', 'audio']
# FitEnum value → CSS object-fit name
_FIT_NAMES  = ['contain', 'cover', 'fill']

logger = logging.getLogger(__name__)
_x_display = None   # second X connection, opened on first use (see CefWidget._sync_browser_size)

_BOOT_URL = (Path(__file__).parent / 'static' / 'boot-screen.html').resolve().as_uri()


def _black_palette() -> QPalette:
    """Palette whose every background role is black.

    A display window must never show anything but black before its content is
    up: the default palette is light, so each newly created window flashes
    white for the frame or two between being mapped and CEF painting over it.
    """
    palette = QPalette()
    black = QColor(0, 0, 0)
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                 QPalette.ColorRole.Button):
        palette.setColor(role, black)
    return palette


class CefWidget(QWidget):
    """Hosts a CEF browser as an X11 child window embedded inside this Qt widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.browser = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Black from the very first frame: between show() and CEF's first
        # paint the widget is filled with the Qt palette's window colour,
        # which on a kiosk reads as a white flash on every window creation.
        self.setAutoFillBackground(True)
        self.setPalette(_black_palette())

    def embed_browser(self, url: str, width: int = None, height: int = None):
        """
        Create the CEF browser child window. Must be called after show().

        width/height should be the caller's already-known target size rather
        than this widget's own self.width()/height(): winId() forces native
        window creation immediately, which can happen before Qt has finished
        laying this widget out to its final size, and no later resizeEvent
        fires to correct it since Qt doesn't consider the size to have
        changed - the browser is then stuck at a stale (often tiny) size.
        """
        winfo = cef.WindowInfo()
        w = max(width or self.width(), 1)
        h = max(height or self.height(), 1)
        winfo.SetAsChild(int(self.winId()), [0, 0, w, h])
        # Same reason as the Qt palette above: a browser's own pre-document
        # colour defaults to opaque white, and it is this one that covers the
        # widget as soon as the CEF child window is mapped. Both the
        # application-wide setting (WebviewApp.cef.Initialize) and this
        # per-browser one are needed - the per-browser value wins where set,
        # and leaving it unset falls back to CefSettings only when its alpha
        # is fully transparent, which is not a state we want to rely on.
        self.browser = cef.CreateBrowserSync(
            winfo, settings={'background_color': 0xFF000000}, url=url)
        # Since the window is already created at its final size, no
        # subsequent resizeEvent (and thus no OnSize call) ever fires to
        # kick off CEF's compositor - it needs at least one explicit OnSize
        # to start painting, particularly with multi_threaded_message_loop
        # (the compositor runs on its own thread and doesn't get an implicit
        # first-paint trigger from our single-threaded message pump anymore).
        # OnSize's signature is (windowHandle, msg, wparam, lparam), a Win32
        # message passthrough reused generically - on Linux it just re-reads
        # the window's current X11 geometry via the handle, so the other
        # three args are unused placeholders (matches every example in
        # vendor/cefpython: gtk2.py, gtk3.py, qt.py all call OnSize(handle,
        # 0, 0, 0)). Passing width/height positionally there is wrong and
        # raises TypeError (wrong arg count) - it was never actually
        # exercised until this call started firing.
        cef.WindowUtils.OnSize(int(self.winId()), 0, 0, 0)
    def execute_js(self, js: str):
        if self.browser:
            self.browser.ExecuteJavascript(js)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.browser:
            cef.WindowUtils.OnSize(int(self.winId()), 0, 0, 0)
            # OnSize reads the widget's X11 geometry through its own connection, and at this point Qt has not flushed
            # the resize to the server yet: the browser kept its old size when the screen changed mode or rotated
            # (measured: window 2160x3840, CEF child still 3840x2160). Ask again once the resize has been applied.
            QTimer.singleShot(0, self._sync_browser_size)
            QTimer.singleShot(100, self._sync_browser_size)

    def _sync_browser_size(self):
        """Make the browser's own X11 window as big as this widget. cefpython's OnSize does not do it on Linux (the
        child keeps the size it was created with), so the child is resized directly over a second X connection."""
        if not self.browser:
            return
        cef.WindowUtils.OnSize(int(self.winId()), 0, 0, 0)
        try:
            from Xlib import display
            global _x_display
            if _x_display is None:
                _x_display = display.Display()
            handle = self.browser.GetWindowHandle()
            if handle:
                _x_display.create_resource_object('window', handle).configure(width=max(self.width(), 1),
                                                                              height=max(self.height(), 1))
                _x_display.flush()
        except Exception as exc:
            logger.warning('could not resize the browser window: %s', exc)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.browser:
            self.browser.SetFocus(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        if self.browser:
            self.browser.SetFocus(False)

    def closeEvent(self, event):
        if self.browser:
            self.browser.CloseBrowser(True)
            self.browser = None
        super().closeEvent(event)


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


class WebviewWindow(QMainWindow, PageCommands):
    """One frameless display window. Multiple instances = multiple screens."""

    def __init__(self, window_id: int, screen: QScreen,
                 x: int, y: int, width: int, height: int):
        super().__init__()
        self._window_id = window_id
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # The window itself is painted before its child widget is, so it needs
        # the same black background - otherwise the flash simply moves one
        # level up. See _black_palette().
        self.setAutoFillBackground(True)
        self.setPalette(_black_palette())
        self.setScreen(screen)
        self.setGeometry(x, y, width, height)
        self._content_width = width
        self._content_height = height

        self._cef = CefWidget(self)
        self.setCentralWidget(self._cef)
        # set by the application: called when the window is moved or resized
        self.on_geometry_changed = None
        # called when the window has been mapped, before its browser is created
        self.on_mapped = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.on_geometry_changed:
            self.on_geometry_changed()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.on_geometry_changed:
            self.on_geometry_changed()

    def closeEvent(self, event):
        # Qt delivers a close event only to the widget close() was called on,
        # never to its children, so CefWidget.closeEvent below does not fire
        # when this window is closed - which is how every window disappears
        # (a screen being unplugged, or a RemoveWindow command). The CEF
        # browser it hosts was therefore never shut down: it outlived its
        # window, keeping its renderer process alive with nothing to draw on.
        # That is the "renderers are never reclaimed" leak, measured going
        # 2 -> 4 -> 5 -> 6 across hot-plug cycles and confirmed on CEF's own
        # DevTools target list, which still listed the removed window's page.
        # Forward the close explicitly so the browser goes with the window.
        self._cef.close()
        super().closeEvent(event)

    def show(self):
        super().show()
        # Mapped, not painted yet (nothing runs the event loop until the browser exists): what shows is white. The
        # application puts its splash back on top before the browser, which takes a while to create, is embedded.
        if self.on_mapped:
            self.on_mapped()
        # embed_browser() needs a valid, visible X11 window handle
        if self._cef.browser is None:
            # the window id in the address is how the DevTools connection (cdp.py) knows which window a page belongs to
            self._cef.embed_browser(f'{_BOOT_URL}?window={self._window_id}', self._content_width, self._content_height)

    def set_bounds(self, x: int, y: int, width: int, height: int):
        self.setGeometry(x, y, width, height)


class _TopLevelBrowser:
    """The CEF browser of a Wayland window: a top-level window of its own, not a child of a Qt widget."""

    def __init__(self):
        self.browser = None

    def execute_js(self, js: str):
        if self.browser:
            self.browser.ExecuteJavascript(js)

    def close(self):
        if self.browser:
            self.browser.CloseBrowser(True)
            self.browser = None


class WaylandWindow(PageCommands):
    """One display window on Wayland. CEF cannot be embedded in a Qt window there (no foreign windows in the protocol), so
    the browser is a top-level window of its own and there is no Qt window at all: the compositor decides where it goes and
    makes it fullscreen on its output (window rules keyed on the title, see wayland.py). Qt is only used to learn about the
    outputs and for the splash. Same interface as WebviewWindow."""

    def __init__(self, window_id: int, screen: QScreen, x: int, y: int, width: int, height: int):
        self._window_id = window_id
        self.screen = screen
        self._cef = _TopLevelBrowser()
        # same attributes as WebviewWindow, set by the application
        self.on_geometry_changed = None
        self.on_mapped = None

    def geometry(self):
        return self.screen.geometry()

    def show(self):
        if self.on_mapped:
            self.on_mapped()
        if self._cef.browser is None:
            # the window id in the address is how the DevTools connection (cdp.py) knows which window a page belongs to,
            # and the page's title ('unitotem-<id>', see boot-screen.html) is how the compositor knows which output it is for
            self._cef.browser = cef.CreateBrowserSync(
                cef.WindowInfo(), settings={'background_color': 0xFF000000},
                url=f'{_BOOT_URL}?window={self._window_id}')

    def set_bounds(self, x: int, y: int, width: int, height: int):
        logger.debug('Window {}: bounds are the compositor\'s business on Wayland, ignoring', self._window_id)

    def close(self):
        self._cef.close()
