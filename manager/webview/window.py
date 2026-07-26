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

    def embed_browser(self, url: str):
        """Create the CEF browser child window. Must be called after show()."""
        winfo = cef.WindowInfo()
        winfo.SetAsChild(int(self.winId()), [0, 0, max(self.width(), 1), max(self.height(), 1)])
        self.browser = cef.CreateBrowserSync(winfo, url=url)

    def execute_js(self, js: str):
        if self.browser:
            self.browser.ExecuteJavascript(js)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.browser:
            cef.WindowUtils.OnSize(int(self.winId()), 0, 0, self.width(), self.height())

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

        self._cef = CefWidget(self)
        self.setCentralWidget(self._cef)

    def show(self):
        super().show()
        # embed_browser() needs a valid, visible X11 window handle
        if self._cef.browser is None:
            self._cef.embed_browser(_BOOT_URL)

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
