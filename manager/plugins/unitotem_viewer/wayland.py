"""Running the viewer on a Wayland compositor instead of an X server (cage, labwc, weston...).

The viewer picks its graphics stack at start: Wayland when `UNITOTEM_VIEWER_PLATFORM=wayland`, or when a Wayland socket is
there and there is no X display; X11 otherwise (the default of the kiosk image). What changes:
  - Qt runs on its wayland platform: it only reports the outputs (QScreen) and draws the splash;
  - each window is a top-level CEF browser (window.WaylandWindow), the browser cannot be embedded in a Qt window;
  - the compositor, not the viewer, puts each window on its output and makes it fullscreen.
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
