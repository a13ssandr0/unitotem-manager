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
import math
import os
import re
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

# The two text lines under the wordmark, as the Plymouth theme draws them. Regular weight: the variable font's default
# instance, which is Regular (wght 400); its variation is not changed.
FONT = HERE / 'static' / 'NotoSans.ttf'
FONT_WIDTH_PER_PT = 90             # point size = screen width / 90 (1280 -> 14, 1920 -> 21, 3840 -> 43)
CAPTION_TOP = 1.2                  # line 1 top below the wordmark bottom, in em (PX)
CAPTION_STEP = 1.82                # line 2 top below line 1 top, in em (PX); calibrated against the Plymouth theme's rendering, 2026-11-26
CAPTION_ALPHA = (0.8, 0.5)         # white opacity of line 1 and line 2
CAPTION_CHARS = 48
CAPTION_DY = 0                     # calibration against the Plymouth frame, in pixels
VIEWER_STATUS = 'Starting viewer'

# The slot and the version of the image: the kernel command line and the release file of the image.
CMDLINE_PATH = Path('/proc/cmdline')
RELEASE_VERSION_PATH = Path('/usr/share/unitotem/release-version')
SLOT_TOKEN = re.compile(r'unitotem\.slot=([ab])')
VERSION = re.compile(r'[0-9A-Za-z.+~:-]+')


def wait_for_compositor(timeout: float = 120.0) -> bool:
    """The compositor's socket: a client cannot connect before it exists. Then, while the compositor unit is still turning the
    outputs as saved (it leaves `output-pending` next to the socket until it is done, at most a few seconds), the first frame waits."""
    name = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
    path = name if os.path.isabs(name) else os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/run/user/0'), name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            pending = os.path.join(os.path.dirname(path), 'output-pending')
            patience = time.monotonic() + 3
            while os.path.exists(pending) and time.monotonic() < patience:
                time.sleep(0.02)
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


def _qimage_rgba(picture):
    """Non-premultiplied RGBA, the layout PIL writes: the alpha of the caption is blended by the painter."""
    from PySide6.QtGui import QImage
    rgba = picture.convert('RGBA')
    return QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def _ascii(text: str) -> str:
    """Printable ASCII only: any other character is shown as '?'."""
    return ''.join(c if 0x20 <= ord(c) < 0x7F else '?' for c in str(text))


def read_slot_and_version() -> tuple[str | None, str | None]:
    """(slot letter 'A' or 'B', version) from the kernel command line and the release file; None for what is missing or
    not valid. Never raises."""
    slot = version = None
    try:
        for token in CMDLINE_PATH.read_text(errors='replace').split():
            match = SLOT_TOKEN.fullmatch(token)
            if match:
                slot = match.group(1).upper()
                break
    except Exception:
        pass
    try:
        first = RELEASE_VERSION_PATH.read_text(errors='replace').splitlines()[0].strip()
        if VERSION.fullmatch(first):
            version = first
    except Exception:
        pass
    return slot, version


def boot_label() -> str:
    """Line 1 of the caption: "Slot A: UniTotem 2026.12", "UniTotem 2026.12" without a slot, "UniTotem" without a version."""
    slot, version = read_slot_and_version()
    label = f'UniTotem {version}' if version else 'UniTotem'
    return f'Slot {slot}: {label}' if slot else label


def caption(width: int, lines) -> tuple:
    """(RGBA image as wide as the screen, top of the image below the wordmark's bottom edge). Line 1 and line 2 are drawn
    in the image, each centred, at most CAPTION_CHARS characters, printable ASCII. The image is None when the font file is
    missing or unreadable: then nothing is drawn. Never logs; the callers do."""
    from PIL import Image, ImageDraw, ImageFont
    width = max(1, int(width))
    px = max(1, round(width / FONT_WIDTH_PER_PT)) * 96 / 72
    step = round(CAPTION_STEP * px)
    offset = round(CAPTION_TOP * px) + CAPTION_DY
    height = step + math.ceil(1.4 * px)       # ascender to descender of the line 2 glyphs
    try:
        font = ImageFont.truetype(str(FONT), px)
    except OSError:
        return None, offset
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for index, text in enumerate(lines[:len(CAPTION_ALPHA)]):
        text = _ascii(text)[:CAPTION_CHARS]
        if not text:
            continue
        x = round((width - font.getlength(text)) / 2)
        draw.text((x, index * step), text, font=font, fill=round(255 * CAPTION_ALPHA[index]), anchor='la')
    image = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    image.putalpha(mask)
    return image, offset


def viewer_caption(width: int) -> tuple:
    """The caption of the viewer's splashes: boot_label() and VIEWER_STATUS, for a screen this wide."""
    return caption(width, (boot_label(), VIEWER_STATUS))


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
            self.caption = None
            try:
                image, self.caption_offset = viewer_caption(width)
                if image is None:
                    print(f'unitotem-splash: no font {FONT}, no text drawn', file=sys.stderr)
                else:
                    self.caption = _qimage_rgba(image)
            except Exception as error:      # the splash stays up without its text
                print(f'unitotem-splash: no caption ({error})', file=sys.stderr)

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
            if self.caption is not None:
                painter.drawImage(0, top + self.picture.height() + self.caption_offset, self.caption)

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
