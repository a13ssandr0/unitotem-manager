"""
The very first frame of a totem: what unitotem-splash.service shows from the moment the compositor is up.

It is started in parallel with the compositor (nothing orders it after it): everything that does not need it is done first
(warm_up), then it waits for the socket and draws at once. A plain script with the lightest imports that can do the job
(QtGui only, no QtWidgets and nothing of the rest of UniTotem: the manager takes many seconds to import on a small board and
the screen stays black for all of them, which is exactly what this frame exists to avoid), Pillow and sdnotify. It draws the
UniTotem wordmark and the spinning ring on every output, tells systemd it is ready, and stays until it is stopped:
the viewer ends it (a D-Bus StopUnit, see boot.py) once its own splash, which looks the same, is on screen. It must stay free
of imports from the rest of UniTotem: it is run as a script, not as part of the plugin package.
"""
import os
import signal
import sys
import time
from pathlib import Path

# The picture is the one of the Plymouth theme (plymouth/unitotem): the same pre-rendered wordmark and ring, the same
# geometry and the same speed, so the hand-over Plymouth -> this -> the viewer shows no change at all. The packaging copies
# the PNGs next to this file; from a source tree they are read from the theme directory.
HERE = Path(__file__).parent
ASSETS = HERE / 'static' / 'bootsplash'
if not ASSETS.is_dir():
    ASSETS = HERE.parents[2] / 'plymouth' / 'unitotem'
TIERS = (1280, 1920, 2560, 3840)
WORDMARK_TO_SCREEN_WIDTH = 0.601   # wordmark width / screen width
RING_CENTER_X_FRAC = 0.5179        # ring centre, as a fraction of the wordmark's width
RING_CENTER_Y_FRAC = 0.5734        # ... and of its height
SPINNER_OVERSAMPLE = 2             # spinner-*.png is drawn at twice the size it is shown at
REVOLUTION = 1.0                   # seconds per turn, clockwise
STEPS = 60                         # pictures per turn of the ring
FRAME = 1 / 30                     # seconds between two frames of the ring


def wait_for_compositor(timeout: float = 120.0) -> bool:
    """The compositor's socket: a client cannot connect before it exists."""
    name = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
    path = name if os.path.isabs(name) else os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/run/user/0'), name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.02)
    return False


_decoded = {}


def decoded(name: str):
    """A PNG of the theme, decoded once: this is done before X is up, so that the first frame is only a resize away."""
    from PIL import Image
    if name not in _decoded:
        _decoded[name] = Image.open(ASSETS / name).convert('RGBA')
        _decoded[name].load()
    return _decoded[name]


def warm_up():
    """Everything that does not need the compositor, done while it is still starting: the imports, and the artwork of every
    size (the screen is not known yet)."""
    import sdnotify  # noqa: F401
    from PySide6 import QtGui  # noqa: F401
    for tier in TIERS:
        decoded(f'wordmark-{tier}.png')
        decoded(f'spinner-{tier}.png')


def artwork(width: int):
    """(wordmark on black, ring, ring centre in the wordmark) for a screen this wide, scaled as the Plymouth theme does."""
    from PIL import Image
    tier = next((t for t in TIERS if t >= width), TIERS[-1])
    mark = decoded(f'wordmark-{tier}.png')
    ring = decoded(f'spinner-{tier}.png')
    scale = int(width * WORDMARK_TO_SCREEN_WIDTH) / mark.width
    mark = mark.resize((int(width * WORDMARK_TO_SCREEN_WIDTH), int(mark.height * scale)), Image.LANCZOS)
    size = int(ring.width * scale / SPINNER_OVERSAMPLE)
    base = Image.new('RGBA', mark.size, 'black')
    base.alpha_composite(mark)
    return base, ring, size, (int(mark.width * RING_CENTER_X_FRAC), int(mark.height * RING_CENTER_Y_FRAC))


def _qimage(picture):
    from PySide6.QtGui import QImage
    rgb = picture.convert('RGB')
    return QImage(rgb.tobytes(), rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()


def _window_class():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter, QRasterWindow

    class SplashWindow(QRasterWindow):
        """The wordmark and the ring of one output. QtGui only: QtWidgets would add to the time the first frame takes."""

        def __init__(self, screen):
            super().__init__()
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
            width = screen.geometry().width()
            self.base_image, self.ring, self.ring_size, centre = artwork(width)
            self.picture = _qimage(self.base_image)
            self.box = (centre[0] - self.ring_size // 2, centre[1] - self.ring_size // 2)
            self.started = time.monotonic()
            self.frames = {}

        def frame(self):
            """The ring at the angle of this moment (clockwise: PIL turns counter-clockwise), over the part of the wordmark
            it sits on. The angle is rounded to a step of the turn and each picture is made once: the first turn is computed
            (a small board is slow at it), the following ones come from the cache."""
            step = int((time.monotonic() - self.started) / REVOLUTION % 1 * STEPS)
            image = self.frames.get(step)
            if image is None:
                from PIL import Image
                ring = self.ring.rotate(-360 * step / STEPS, Image.BICUBIC).resize((self.ring_size,) * 2, Image.LANCZOS)
                spot = self.base_image.crop((*self.box, self.box[0] + self.ring_size, self.box[1] + self.ring_size))
                spot.alpha_composite(ring)
                image = self.frames[step] = _qimage(spot)
            return image

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.fillRect(QRect(0, 0, self.width(), self.height()), 0x000000)
            left = (self.width() - self.picture.width()) // 2
            top = (self.height() - self.picture.height()) // 2
            painter.drawImage(left, top, self.picture)
            painter.drawImage(left + self.box[0], top + self.box[1], self.frame())

    return SplashWindow


def main() -> int:
    warm_up()
    if not wait_for_compositor():
        print('unitotem-splash: no compositor', file=sys.stderr)
        return 1
    os.environ['QT_QPA_PLATFORM'] = 'wayland'
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication(sys.argv[:1])
    window_class = _window_class()
    windows = [window_class(screen) for screen in app.screens() if not screen.geometry().isEmpty()]
    for window in windows:
        window.showFullScreen()
    timer = QTimer()
    timer.timeout.connect(lambda: [w.update() for w in windows])
    timer.start(int(FRAME * 1000))
    ready = QTimer()
    ready.setSingleShot(True)

    def notify():
        try:
            from sdnotify import SystemdNotifier
            SystemdNotifier().notify('READY=1')
        except ImportError:
            pass
    ready.timeout.connect(notify)
    ready.start(300)        # the first frame has been painted by then

    # systemd stops the service with SIGTERM: Python's handler only runs while Python code does, so give it a heartbeat
    # The viewer ends this process once its own splash is showing. Qt's orderly teardown of Wayland windows segfaulted at that moment (status
    # 11 in the journal, measured on a Pi 4 with the 2026.10.31 image): there is nothing to save, leave at once; the compositor removes the
    # windows when the connection closes
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    signal.signal(signal.SIGINT, lambda *_: os._exit(0))
    beat = QTimer()
    beat.timeout.connect(lambda: None)
    beat.start(200)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
