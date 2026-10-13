"""
The first thing on a screen: a black frame with the UniTotem wordmark and a ring, drawn by Qt alone.

CEF takes seconds to start (much longer on an SD card) and nothing can be painted by it before that, so the boot splash
of the system would have to wait for it. This frame is on screen within a second of the viewer starting; it is closed
once the browser has taken over. It is static on purpose: the thread that would animate it is the one busy starting CEF.

The module is also a program (`unitotem-splash.service`): started as soon as X is up, it imports nothing but Qt, so the
frame is on screen within a second or two even on a slow board, long before the manager has finished importing its own
dependencies. The viewer stops it once its own splash is showing, so there is no gap between the two. It must therefore
stay free of imports from the rest of UniTotem: it is run as a plain script, not as part of the plugin package.
"""
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import QWidget

_FONT = Path(__file__).parent / 'static' / 'NotoSans.ttf'
_ACCENT = QColor('#FF3D00')


class Splash(QWidget):
    def __init__(self, screen):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.BlankCursor)
        family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(_FONT)))
        self._family = family[0] if family else 'sans-serif'

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        height = self.height()
        font = QFont(self._family)
        font.setPixelSize(max(12, height // 10))
        font.setWeight(QFont.Weight.Light)
        painter.setFont(font)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QRectF(0, 0, self.width(), height), Qt.AlignmentFlag.AlignCenter, 'UniTotem')
        # a ring under the wordmark, with the gap a spinner has
        size = height // 14
        pen = QPen(_ACCENT, max(2, size // 8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF((self.width() - size) / 2, height * 0.62, size, size), 90 * 16, -270 * 16)


def show_splashes(screens) -> list[Splash]:
    """One per screen, painted before this returns."""
    from PySide6.QtWidgets import QApplication
    splashes = [Splash(screen) for screen in screens]
    for splash in splashes:
        splash.show()
    QApplication.processEvents()
    return splashes


def _wait_for_x(timeout: float = 120.0) -> bool:
    """The X socket and the cookie the manager's session writes for it, both needed before Qt can connect."""
    import os
    import time
    number = os.environ.get('DISPLAY', ':0').split(':', 1)[-1].split('.', 1)[0]
    auth = os.environ.get('XAUTHORITY')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(f'/tmp/.X11-unix/X{number}') and (auth is None or os.path.exists(auth)):
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    import signal
    import sys

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    if not _wait_for_x():
        print('unitotem-splash: no X display', file=sys.stderr)
        return 1
    app = QApplication(sys.argv[:1])
    splashes = show_splashes(app.screens())
    try:
        from sdnotify import SystemdNotifier
        SystemdNotifier().notify('READY=1')
    except ImportError:
        pass
    # Python only runs its signal handlers while it executes bytecode, which a Qt loop does not do by itself
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(200)
    app.exec()
    for splash in splashes:
        splash.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
