"""
The first thing on a screen: a black frame with the UniTotem wordmark and a ring, drawn by Qt alone.

CEF takes seconds to start (much longer on an SD card) and nothing can be painted by it before that, so the boot splash
of the system would have to wait for it. This frame is on screen within a second of the viewer starting; it is closed
once the browser has taken over. It is static on purpose: the thread that would animate it is the one busy starting CEF.

The very first frame of the boot, before the manager has even finished importing, is a separate light program
(bootsplash.py, unitotem-splash.service); this one takes over from it inside the viewer and looks the same.
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
