"""Running the viewer on a Wayland compositor instead of an X server (cage, labwc, weston...).

The viewer picks its graphics stack at start: Wayland when `UNITOTEM_VIEWER_PLATFORM=wayland`, or when a Wayland socket is
there and there is no X display; X11 otherwise (the default of the kiosk image). What changes:
  - Qt runs on its wayland platform: it only reports the outputs (QScreen) and draws the splash;
  - each window is a top-level CEF browser (window.WaylandWindow), the browser cannot be embedded in a Qt window;
  - the compositor, not the viewer, puts each window on its output and makes it fullscreen (see place_next).
"""
import os


def wanted() -> bool:
    forced = os.environ.get('UNITOTEM_VIEWER_PLATFORM', '').lower()
    if forced:
        return forced == 'wayland'
    return bool(os.environ.get('WAYLAND_DISPLAY')) and not os.environ.get('DISPLAY')


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
# $UNITOTEM_LABWC_CONFIG is the rc.xml (set by whoever starts labwc), $LABWC_PID is exported by labwc itself. Without
# them (cage, a desktop compositor...) nothing is done and the compositor places the window.
import fcntl
import html
import logging
import shutil
import signal
import struct
import subprocess
import time

logger = logging.getLogger(__name__)
_RC = ('<?xml version="1.0"?>\n<labwc_config>\n  <core><gap>0</gap></core>\n  <theme><name></name><cornerRadius>0</cornerRadius></theme>\n'
       '  <windowRules>\n    <windowRule type="normal" serverDecoration="no"/>\n  </windowRules>\n'
       '  <keyboard>\n    <default />\n    <keybind key="W-A-F12">\n'
       '      <action name="MoveToOutput" output="{output}"/>\n      <action name="Maximize"/>\n    </keybind>\n  </keyboard>\n'
       '</labwc_config>\n')
SETTLE_S = 0.25      # time labwc gets to re-read its configuration, and to act on the keys
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
    path, pid = os.environ.get('UNITOTEM_LABWC_CONFIG'), os.environ.get('LABWC_PID')
    if not path or not pid or not shutil.which('wlrctl') or not os.path.exists('/dev/uinput'):
        return False
    try:
        tmp = f'{path}.tmp'
        with open(tmp, 'w') as f:
            f.write(_RC.format(output=html.escape(output)))
        os.replace(tmp, path)
        os.kill(int(pid), signal.SIGHUP)
        _virtual_keyboard()
        time.sleep(SETTLE_S)
        return True
    except (OSError, ValueError) as exc:
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
