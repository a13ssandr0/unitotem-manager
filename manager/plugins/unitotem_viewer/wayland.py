"""The viewer on a Wayland compositor (labwc on the kiosk image).

  - Qt runs on its wayland platform: it only reports the outputs (QScreen) and draws the splash;
  - each window is a frameless top-level CEF window (window.WebviewWindow, the Views API), the browser cannot be embedded in a Qt window;
  - the window asks for fullscreen on its own output (xdg_toplevel.set_fullscreen(output)): nothing is told to the compositor.
"""
import os


def socket_available() -> bool:
    name = os.environ.get('WAYLAND_DISPLAY', 'wayland-0')
    path = name if os.path.isabs(name) else os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/run/user/0'), name)
    return os.path.exists(path)
