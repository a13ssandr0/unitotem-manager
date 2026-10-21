"""The viewer on a Wayland compositor (labwc on the kiosk image).

  - Qt runs on its wayland platform: it only reports the outputs (QScreen) and draws the splash;
  - each window is a top-level CEF browser (window.WebviewWindow), the browser cannot be embedded in a Qt window;
  - the compositor, not the viewer, puts each window on its output and makes it fullscreen (see prepare/apply below).
"""
import os


def socket_available() -> bool:
    name = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
    path = name if os.path.isabs(name) else os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/run/user/0'), name)
    return os.path.exists(path)


# ── placing the windows: a labwc key binding pressed through a virtual keyboard ────────────────────────────────────────
# A Wayland client cannot say where its window goes, and CEF's windows carry neither a title nor an application id that a
# window rule could match (both empty). What labwc can do is act on its ACTIVE window, and a window that has just appeared
# is the active one. So, for each window: rc.xml gets a key binding (Super+Alt+F12) that moves the active window to the
# window's output and maximizes it (no decorations, no panels: it fills the output; both actions can be repeated harmlessly), labwc is told to re-read it (SIGHUP; a key binding, unlike a window rule, is not
# applied again to the windows that exist), the window is created, and once the compositor lists it (`wlrctl`, the
# wlr-foreign-toplevel protocol) a virtual keyboard (uinput) presses the keys. Windows are created one at a time.
# The key binding is the viewer's fragment of the compositor configuration (utils/labwc.py). Without labwc (cage, a desktop
# compositor...) nothing is done and the compositor places the window.
import fcntl
import html
import logging
import shutil
import struct
import subprocess
import time

from utils import labwc

logger = logging.getLogger(__name__)
FRAGMENT = 'viewer'
_BODY = ('''  <windowRules>
    <windowRule type="normal" serverDecoration="no"/>
  </windowRules>
  <keyboard>
    <default />
    <keybind key="W-A-F12">
      <action name="MoveToOutput" output="{output}"/>
      <action name="Maximize"/>
    </keybind>
  </keyboard>
''')
SETTLE_S = 0.25      # time labwc gets to re-read its configuration, and to act on the keys
MAP_TIMEOUT_S = 8.0  # most a window gets to be listed by the compositor
_KEYS = (125, 56, 88)    # KEY_LEFTMETA, KEY_LEFTALT, KEY_F12
_keyboard_fd = None


def _virtual_keyboard() -> int:
    """A uinput keyboard that can press the keys above; created on first use and kept."""
    global _keyboard_fd
    if _keyboard_fd is not None:
        return _keyboard_fd
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_SETUP, UI_DEV_CREATE = 0x40045564, 0x40045565, 0x405c5503, 0x5501
    fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
    fcntl.ioctl(fd, UI_SET_EVBIT, 1)        # EV_KEY
    for key in _KEYS:
        fcntl.ioctl(fd, UI_SET_KEYBIT, key)
    # struct uinput_setup: input_id (bustype, vendor, product, version), name[80], ff_effects_max
    fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack('<HHHH80sI', 0x03, 0x1209, 0x7001, 1, b'UniTotem virtual keyboard', 0))
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(1.0)                         # libinput and the compositor learn about the new device
    _keyboard_fd = fd
    return fd


def _event(fd: int, key: int, value: int):
    os.write(fd, struct.pack('<qqHHi', 0, 0, 1, key, value))       # EV_KEY
    os.write(fd, struct.pack('<qqHHi', 0, 0, 0, 0, 0))             # EV_SYN


def prepare(output: str) -> bool:
    """Gets the compositor ready to put the next window on `output`. False when there is no compositor to tell."""
    if labwc.pid() is None or not shutil.which('wlrctl') or not os.path.exists('/dev/uinput'):
        return False
    try:
        if not labwc.write_fragment(FRAGMENT, _BODY.format(output=html.escape(output))) or not labwc.reload():
            return False
        _virtual_keyboard()
        time.sleep(SETTLE_S)
        return True
    except OSError as exc:
        logger.warning('Cannot prepare the compositor for %s: %s', output, exc)
        return False


def apply() -> None:
    """Presses the key binding of prepare(): the active window (the one that just appeared) goes to its output."""
    try:
        fd = _virtual_keyboard()
        for key in _KEYS:
            _event(fd, key, 1)
        for key in reversed(_KEYS):
            _event(fd, key, 0)
        time.sleep(SETTLE_S)
    except OSError as exc:
        logger.warning('Cannot move the window to its output: %s', exc)


def toplevel_count() -> int:
    """How many windows the compositor lists (wlr-foreign-toplevel, through wlrctl): the way to know a new one has appeared."""
    try:
        out = subprocess.run(['wlrctl', 'toplevel', 'list'], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return len([line for line in out.splitlines() if line.strip()])
