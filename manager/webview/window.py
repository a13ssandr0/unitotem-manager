"""
WebviewWindow — frameless Qt6 window with an embedded CEF browser.

CEF renders directly to an X11 child window (via SetAsChild / winId()),
so Wayland must be backed by XWayland (QT_QPA_PLATFORM=xcb).
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QScreen
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


class CefWidget(QWidget):
    """Hosts a CEF browser as an X11 child window embedded inside this Qt widget."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.browser = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
        self.browser = cef.CreateBrowserSync(winfo, url=url)
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
        # Rotation is compositor-level on Wayland / Xrandr on X11.
        # Apply it via CSS transform inside the page so CEF handles it regardless.
        deg = orientation % 360
        self._cef.execute_js(
            f'document.body.style.transform="rotate({deg}deg)";'
            f'document.body.style.transformOrigin="center center";'
        )

    def set_flip(self, flip: int):
        # flip: 0=none, 1=horizontal, 2=vertical, 3=both
        sx = -1 if flip & 1 else 1
        sy = -1 if flip & 2 else 1
        self._cef.execute_js(
            f'document.body.style.transform+=" scaleX({sx}) scaleY({sy})";'
        )
