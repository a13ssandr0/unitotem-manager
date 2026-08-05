"""
WebviewWindow — frameless Qt6 window with an embedded CEF browser.

CEF renders directly to an X11 child window (via SetAsChild / winId()),
so Wayland must be backed by XWayland (QT_QPA_PLATFORM=xcb).
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt
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


class WebviewWindow(QMainWindow):
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

    def show(self):
        super().show()
        # embed_browser() needs a valid, visible X11 window handle
        if self._cef.browser is None:
            self._cef.embed_browser(_BOOT_URL, self._content_width, self._content_height)

    # ── commands sent by the manager via WebSocket ────────────────────────

    def show_asset(self, src: str, container: int, fit: int, bg_color: str):
        """
        Display an asset.

        container: index into ['boot','web','image','video','audio'] (= media_type + 1)
        fit:       FitEnum int value → 'contain' | 'cover' | 'fill'
        bg_color:  CSS colour string e.g. 'rgb(0,0,0)'
        """
        cont = _CONTAINERS[container] if 0 <= container < len(_CONTAINERS) else 'boot'
        fit_s = _FIT_NAMES[fit] if 0 <= fit < len(_FIT_NAMES) else 'contain'
        self._cef.execute_js(
            f'show({json.dumps(src)}, {json.dumps(cont)}, {json.dumps(fit_s)}, {json.dumps(bg_color)})'
        )

    def set_bounds(self, x: int, y: int, width: int, height: int):
        self.setGeometry(x, y, width, height)

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
