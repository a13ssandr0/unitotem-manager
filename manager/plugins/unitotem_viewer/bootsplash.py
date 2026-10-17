"""
The very first frame of a totem: what unitotem-splash.service shows from the moment the X server is up.

It is started in parallel with the X server (nothing orders it after it): everything that does not need X is done first (warm_up),
then it waits for the socket and draws at once. A plain script with the lightest imports that can do the job (python-xlib, Pillow, sdnotify): no Qt. Importing PySide6 takes
several seconds on a small board and the screen stays black for all of them, which is exactly what this frame exists to
avoid. It draws the UniTotem wordmark and the spinning ring on every monitor, tells systemd it is ready, and stays until it is stopped:
the viewer ends it (a D-Bus StopUnit, see boot.py) once its own splash, which looks the same, is on screen. It must stay free
of imports from the rest of UniTotem: it is run as a script, not as part of the plugin package.
"""
import os
import select
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


def wait_for_x(timeout: float = 120.0) -> bool:
    """The X socket and the cookie the session writes for it: both are needed before a client can connect."""
    number = os.environ.get('DISPLAY', ':0').split(':', 1)[-1].split('.', 1)[0]
    auth = os.environ.get('XAUTHORITY')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(f'/tmp/.X11-unix/X{number}') and (auth is None or os.path.exists(auth)):
            return True
        time.sleep(0.02)
    return False


def monitors(display):
    """(x, y, width, height) of every monitor, the whole screen when RandR says nothing."""
    from Xlib.ext import randr
    root = display.screen().root
    try:
        found = [(m.x, m.y, m.width_in_pixels, m.height_in_pixels) for m in randr.get_monitors(root, True).monitors]
    except Exception:
        found = []
    return found or [(0, 0, display.screen().width_in_pixels, display.screen().height_in_pixels)]


_decoded = {}


def decoded(name: str):
    """A PNG of the theme, decoded once: this is done before X is up, so that the first frame is only a resize away."""
    from PIL import Image
    if name not in _decoded:
        _decoded[name] = Image.open(ASSETS / name).convert('RGBA')
        _decoded[name].load()
    return _decoded[name]


def warm_up():
    """Everything that does not need the X server, done while it is still starting: the imports, and the artwork of every
    size (the screen is not known yet)."""
    import Xlib.display, Xlib.ext.randr, Xlib.X  # noqa: F401
    import sdnotify  # noqa: F401
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


class Splash:
    def __init__(self, display, area):
        from Xlib import X
        self.display = display
        self.x, self.y, self.width, self.height = area
        screen = display.screen()
        self.window = screen.root.create_window(
            self.x, self.y, self.width, self.height, 0, screen.root_depth, X.InputOutput, X.CopyFromParent,
            background_pixel=screen.black_pixel, override_redirect=True, event_mask=X.ExposureMask)
        self.gc = self.window.create_gc(foreground=screen.white_pixel, background=screen.black_pixel)
        self.base, self.ring, self.ring_size, centre = artwork(self.width)
        self.left = (self.width - self.base.width) // 2
        self.top = (self.height - self.base.height) // 2
        self.box = (centre[0] - self.ring_size // 2, centre[1] - self.ring_size // 2)
        self.started = time.monotonic()
        self.frames = {}
        self.window.map()

    def draw(self):
        self.window.clear_area(0, 0, self.width, self.height)
        self.window.put_pil_image(self.gc, self.left, self.top, self.base.convert('RGB'))
        self.spin()

    def spin(self):
        """The ring at the angle of this moment (clockwise: PIL turns counter-clockwise), over the part of the wordmark
        it sits on. The angle is rounded to a step of the turn and each picture is made once: the first turn is computed
        (a small board is slow at it), the following ones come from the cache."""
        step = int((time.monotonic() - self.started) / REVOLUTION % 1 * STEPS)
        frame = self.frames.get(step)
        if frame is None:
            from PIL import Image
            ring = self.ring.rotate(-360 * step / STEPS, Image.BICUBIC).resize((self.ring_size,) * 2, Image.LANCZOS)
            spot = self.base.crop((*self.box, self.box[0] + self.ring_size, self.box[1] + self.ring_size))
            spot.alpha_composite(ring)
            frame = self.frames[step] = spot.convert('RGB')
        self.window.put_pil_image(self.gc, self.left + self.box[0], self.top + self.box[1], frame)


def connect(timeout: float = 30.0):
    """The X display, retried briefly: the socket can exist a moment before the server accepts on it."""
    from Xlib import display as xdisplay
    from Xlib.error import DisplayConnectionError
    deadline = time.monotonic() + timeout
    while True:
        try:
            return xdisplay.Display()
        except (DisplayConnectionError, ConnectionError, OSError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def main() -> int:
    warm_up()
    if not wait_for_x():
        print('unitotem-splash: no X display', file=sys.stderr)
        return 1
    from Xlib import X
    display = connect()
    splashes = [Splash(display, area) for area in monitors(display)]
    for splash in splashes:
        splash.draw()
    display.flush()
    try:
        from sdnotify import SystemdNotifier
        SystemdNotifier().notify('READY=1')
    except ImportError:
        pass

    # Wait for what matters, with no polling: a redraw request from X, or a signal (systemd stopping the service)
    wake_read, wake_write = os.pipe()
    os.set_blocking(wake_write, False)
    signal.set_wakeup_fd(wake_write)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: None)
    while True:
        while display.pending_events():
            event = display.next_event()
            if event.type == X.Expose and event.count == 0:
                for splash in splashes:
                    if splash.window.id == event.window.id:
                        splash.draw()
                display.flush()
        ready, _, _ = select.select([display.fileno(), wake_read], [], [], FRAME)
        if wake_read in ready:
            return 0
        for splash in splashes:
            splash.spin()
        display.flush()


if __name__ == '__main__':
    raise SystemExit(main())
