"""
The very first frame of a totem: what unitotem-splash.service shows from the moment the X server is up.

A plain script with the lightest imports that can do the job (python-xlib, Pillow, sdnotify): no Qt. Importing PySide6 takes
several seconds on a small board and the screen stays black for all of them, which is exactly what this frame exists to
avoid. It draws the UniTotem wordmark and a ring on every monitor, tells systemd it is ready, and stays until it is stopped:
the viewer ends it (a D-Bus StopUnit, see boot.py) once its own splash, which looks the same, is on screen. It must stay free
of imports from the rest of UniTotem: it is run as a script, not as part of the plugin package.
"""
import os
import select
import signal
import sys
import time
from pathlib import Path

FONT = Path(__file__).parent / 'static' / 'NotoSans.ttf'
ACCENT = '#FF3D00'


def wait_for_x(timeout: float = 120.0) -> bool:
    """The X socket and the cookie the session writes for it: both are needed before a client can connect."""
    number = os.environ.get('DISPLAY', ':0').split(':', 1)[-1].split('.', 1)[0]
    auth = os.environ.get('XAUTHORITY')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(f'/tmp/.X11-unix/X{number}') and (auth is None or os.path.exists(auth)):
            return True
        time.sleep(0.1)
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


def wordmark(height: int):
    """The wordmark as an image, white on black."""
    from PIL import Image, ImageDraw, ImageFont
    size = max(12, height // 10)
    font = ImageFont.truetype(str(FONT), size)
    box = font.getbbox('UniTotem')
    image = Image.new('RGB', (box[2] - box[0] + 4, box[3] - box[1] + 4), 'black')
    ImageDraw.Draw(image).text((2 - box[0], 2 - box[1]), 'UniTotem', font=font, fill='white')
    return image


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
        self.ring_gc = self.window.create_gc(foreground=screen.default_colormap.alloc_named_color(ACCENT).pixel,
                                             line_width=max(2, self.height // 112), cap_style=X.CapRound)
        self.image = wordmark(self.height)
        self.window.map()

    def draw(self):
        image = self.image
        self.window.clear_area(0, 0, self.width, self.height)
        self.window.put_pil_image(self.gc, (self.width - image.width) // 2, (self.height - image.height) // 2, image)
        size = max(8, self.height // 14)
        # the gap a spinner has: 270 degrees from the top, clockwise (Xlib angles are in 64ths of a degree)
        self.window.arc(self.ring_gc, (self.width - size) // 2, int(self.height * 0.62), size, size, 90 * 64, -270 * 64)


def main() -> int:
    if not wait_for_x():
        print('unitotem-splash: no X display', file=sys.stderr)
        return 1
    from Xlib import X, display as xdisplay
    display = xdisplay.Display()
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
        ready, _, _ = select.select([display.fileno(), wake_read], [], [])
        if wake_read in ready:
            return 0


if __name__ == '__main__':
    raise SystemExit(main())
