"""
The first thing on a screen: the same picture as the boot (wordmark and ring), drawn by Qt.

CEF takes seconds to start (much longer on an SD card) and nothing can be painted by it before that, so the boot splash
of the system would have to wait for it. This frame is on screen within a second of the viewer starting; it is closed
once the browser has taken over. It is static on purpose: the thread that would animate it is the one busy starting CEF.

The very first frame of the boot, before the manager has even finished importing, is a separate light program
(bootsplash.py, unitotem-splash.service); this one takes over from it inside the viewer and looks the same.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

from .bootsplash import artwork


class Splash(QWidget):
    """The picture of bootsplash.py (the Plymouth theme's own wordmark and ring, same geometry), still: the thread that
    would turn the ring is the one busy starting CEF. Plymouth, bootsplash and this are one identical picture."""

    def __init__(self, screen):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.painted = False
        self._picture = self._compose(screen.geometry().width())

    @staticmethod
    def _compose(width: int) -> QImage:
        from PIL import Image
        base, ring, size, centre = artwork(width)
        picture = base.copy()
        ring = ring.resize((size, size), Image.LANCZOS)
        picture.alpha_composite(ring, (centre[0] - size // 2, centre[1] - size // 2))
        rgb = picture.convert('RGB')
        image = QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
        return image.copy()     # owns its pixels: the bytes above are freed

    def paintEvent(self, _event):
        self.painted = True
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.drawImage((self.width() - self._picture.width()) // 2, (self.height() - self._picture.height()) // 2,
                          self._picture)


def show_splashes(screens, timeout: float = 3.0) -> list[Splash]:
    """One per screen, ON SCREEN with their pixels painted before this returns: whoever called asks for the previous frame
    to be removed right after, and showing a window is not painting it (on a slow board the first paint comes several event
    loop turns after the window is mapped, and the gap between the two frames is a black screen)."""
    import time
    from PySide6.QtWidgets import QApplication
    splashes = [Splash(screen) for screen in screens]
    for splash in splashes:
        splash.show()
    deadline = time.monotonic() + timeout
    while not all(splash.painted for splash in splashes) and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    for splash in splashes:
        splash.repaint()            # synchronous, in case the first paint was only scheduled
    QApplication.processEvents()
    return splashes
