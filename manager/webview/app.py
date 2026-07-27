"""
WebviewApp — Qt6+CEF webview integrated into the UniTotem manager process.

Architecture
───────────
  Main thread   : Qt event loop, pumping CEF's message loop via a QTimer
  asyncio thread: WebSocket client that receives manager commands

multi_threaded_message_loop is a Windows-only feature in upstream CEF (see
vendor/cefpython/docs/Tutorial.md "Windows: multi-threaded message loop" and
api/ApplicationSettings.md, both state it is unsupported outside Windows) -
on Linux it is silently a no-op at the C++ level, so it must stay False and
CEF's queue must be pumped externally via cef.MessageLoopWork().

Commands from the WS thread are forwarded to Qt via Signal/Slot,
which is the only thread-safe bridge between the two event loops.
"""
import asyncio
import json
import os
import socket
import ssl
import threading
from base64 import b64decode
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication

from .window import WebviewWindow

try:
    from cefpython3 import cefpython as cef
except ImportError:
    cef = None

# Loopback-only Chrome DevTools Protocol port (CEF/Chromium default-binds
# remote-debugging-port to 127.0.0.1, not 0.0.0.0, but 'remote-debugging-
# address' is set explicitly below for defense in depth). Used by
# api.display.getGPUFeatureStats() to query SystemInfo.getInfo - the same
# underlying data chrome://gpu and Electron's old app.getGPUFeatureStatus()
# both surface. Not a secret; just needs to not collide with anything else.
CDP_PORT = 9223


class _Bridge(QObject):
    """Carries commands from the asyncio/WS thread to the Qt main thread."""
    command_received = Signal(dict)


class WebviewApp:
    """
    Initialise and run the local Qt6+CEF webview.

    Parameters
    ----------
    manager_url  : WebSocket URL of the manager's /remote endpoint
                   (e.g. 'wss://localhost:443/remote')
    instance_id  : Unique identifier for this webview instance
    """

    @staticmethod
    def display_available() -> bool:
        """
        Whether a reachable X11 display exists. CEF only supports X11 (a real
        X server or XWayland - see the QT_QPA_PLATFORM override below), so
        this must be checked *before* ever touching Qt/CEF: constructing
        either with no usable platform plugin aborts the whole process
        natively (SIGABRT), which no Python exception handler can catch.
        """
        display = os.environ.get('DISPLAY')
        if not display:
            return False
        num = display.split(':', 1)[-1].split('.', 1)[0]
        return num.lstrip('-').isdigit() and os.path.exists(f'/tmp/.X11-unix/X{num}')

    def __init__(self, manager_url: str, instance_id: str):
        if cef is None:
            raise ImportError(
                "cefpython3 is not installed. "
                "Install the linesight fork: https://github.com/linesight/cefpython"
            )

        self.manager_url = manager_url
        self.instance_id = instance_id
        self.hostname    = socket.gethostname()

        # CEF requires an X11 window handle (winId). On Wayland we fall back
        # to XWayland by forcing the xcb Qt platform plugin.
        if os.environ.get('WAYLAND_DISPLAY') and 'QT_QPA_PLATFORM' not in os.environ:
            os.environ['QT_QPA_PLATFORM'] = 'xcb'

        # cef.Initialize() below unconditionally attaches its own internal
        # GLib sources (sandbox IPC, hang watcher, etc.) to the process's
        # default GMainContext. On Linux, Qt's own event dispatcher
        # (QEventDispatcherGlib) also iterates that same default context by
        # default - so qt_app.exec() ends up pumping CEF's internal sources
        # itself, far more aggressively than our own paced QTimer, pinning a
        # full core at ~100% even with a fully static page and regardless of
        # the QTimer's interval (both verified empirically). Forcing Qt onto
        # QEventDispatcherUNIX (plain epoll, no GMainContext involvement)
        # eliminates this entirely; CEF's message loop is then driven solely
        # and correctly by the explicit cef.MessageLoopWork() QTimer in run().
        if 'QT_NO_GLIB' not in os.environ:
            os.environ['QT_NO_GLIB'] = '1'

        # CEF's default profile dir is under $HOME/.config, which on the
        # kiosk image is only writable during maintenance windows (the root
        # filesystem is otherwise read-only) - CEF's ProcessSingleton lock
        # file then can't be created/replaced and it aborts. /var/cache is a
        # tmpfs on that image (always writable); an ephemeral cache is fine
        # for a kiosk browser anyway (nothing here needs to survive reboots).
        cache_path = '/var/cache/unitotem-cef'
        os.makedirs(cache_path, exist_ok=True)

        # multi_threaded_message_loop must stay False - it's a Windows-only
        # feature in this fork (see module docstring); CEF's queue is instead
        # pumped from a QTimer in run() (the pattern used by every cefpython
        # example). See the QT_NO_GLIB comment above for the fix to the CPU
        # busy-loop this used to cause.
        cef.Initialize(
            settings={
                'windowless_rendering_enabled' : False,
                'multi_threaded_message_loop'  : False,
                'log_severity'                 : cef.LOGSEVERITY_WARNING,
                'remote_debugging_port'        : CDP_PORT,
                'cache_path'                   : cache_path,
            },
            switches={
                'no-proxy-server'  : '',
                'disable-extensions': '',
                # Allow videos and audio to auto-play without user interaction
                'autoplay-policy'  : 'no-user-gesture-required',
                'remote-debugging-address': '127.0.0.1',
            },
        )

        self.qt_app = QApplication.instance() or QApplication([])
        self.qt_app.setApplicationName('UniTotem')

        self._bridge = _Bridge()
        self._bridge.command_received.connect(self._handle_command)

        self.windows: dict[int, WebviewWindow] = {}
        self._screen_windows: dict[QScreen, int] = {}
        self._next_id = 0
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None

        # One fullscreen window per currently-connected screen (zero screens
        # means zero windows: the app must be able to start without any and
        # keep running, picking up screens as they're connected below).
        for screen in self.qt_app.screens():
            self._open_window_for_screen(screen)
        self.qt_app.screenAdded.connect(self._open_window_for_screen)
        self.qt_app.screenRemoved.connect(self._on_screen_removed)

    # ── window management ─────────────────────────────────────────────────

    def _open_window_for_screen(self, screen: QScreen) -> int:
        """Open a fullscreen window on a newly-detected screen. No playlist
        is assigned automatically (assignment is a separate, explicit step)."""
        geom = screen.geometry()
        win = WebviewWindow(
            window_id=self._next_id,
            screen=screen,
            x=geom.x(), y=geom.y(),
            width=geom.width(), height=geom.height(),
        )
        win.show()
        self.windows[self._next_id] = win
        self._screen_windows[screen] = self._next_id
        self._next_id += 1
        return self._next_id - 1

    def _on_screen_removed(self, screen: QScreen):
        """Close the window associated with a screen that just disconnected."""
        window_id = self._screen_windows.pop(screen, None)
        if window_id is not None:
            win = self.windows.pop(window_id, None)
            if win is not None:
                win.close()

    def _open_window(self, screen_index: int = 0,
                     x: int = 0, y: int = 0,
                     width: int = 0, height: int = 0) -> int:
        """Open an extra window on an already-connected screen, at a
        possibly custom size (used by the remote 'AddWindow' command)."""
        screens = self.qt_app.screens()
        if not screens:
            raise RuntimeError('No screens connected')
        screen  = screens[min(screen_index, len(screens) - 1)]
        geom    = screen.geometry()
        win = WebviewWindow(
            window_id=self._next_id,
            screen=screen,
            x=x or geom.x(),
            y=y or geom.y(),
            width=width or geom.width(),
            height=height or geom.height(),
        )
        win.show()
        self.windows[self._next_id] = win
        self._next_id += 1
        return self._next_id - 1

    # ── entry point ───────────────────────────────────────────────────────

    def run(self):
        """
        Start the WS client in a background thread then run Qt+CEF on this thread.
        Returns when the Qt event loop exits.
        """
        self._ws_loop = asyncio.new_event_loop()
        ws_thread = threading.Thread(
            target=self._ws_thread_main, name='webview-ws', daemon=True
        )
        ws_thread.start()

        cef_timer = QTimer()
        cef_timer.timeout.connect(cef.MessageLoopWork)
        cef_timer.start(10)

        exit_code = self.qt_app.exec()
        cef.Shutdown()
        return exit_code

    # ── WebSocket client (asyncio background thread) ──────────────────────

    def _ws_thread_main(self):
        asyncio.set_event_loop(self._ws_loop)
        self._ws_loop.run_until_complete(self._ws_client())

    async def _ws_client(self):
        import websockets

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode    = ssl.CERT_NONE

        while True:
            try:
                async with websockets.connect(
                    self.manager_url,
                    additional_headers={
                        'instance_id': self.instance_id,
                        'hostname'   : self.hostname,
                    },
                    ssl=ssl_ctx,
                ) as ws:
                    await self._send_webview_info(ws)
                    async for raw in ws:
                        try:
                            self._bridge.command_received.emit(self._decode_frame(raw))
                        except Exception:
                            pass
            except Exception:
                # Retry until the manager is up (e.g. still starting)
                await asyncio.sleep(3)

    @staticmethod
    def _decode_frame(raw: str) -> dict:
        """The manager signs every command: frames are 'b64(json).b64(signature)'
        (see WSManager.prepare_message); plain JSON is accepted as well."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            data, _, _signature = raw.partition('.')
            return json.loads(b64decode(data))

    async def _send_webview_info(self, ws):
        """Announce screen geometry and open windows to the manager."""
        screens = []
        for i, s in enumerate(self.qt_app.screens()):
            g = s.geometry()
            screens.append({
                'index': i, 'name': s.name(),
                'x': g.x(), 'y': g.y(),
                'width': g.width(), 'height': g.height(),
            })
        windows = []
        for wid, win in self.windows.items():
            g = win.geometry()
            windows.append({
                'window_id': wid,
                'x': g.x(), 'y': g.y(),
                'width': g.width(), 'height': g.height(),
            })
        await ws.send(json.dumps({
            'target' : 'WebviewInfo',
            'screens': screens,
            'windows': windows,
        }))

    # ── command dispatch (Qt main thread via Signal) ──────────────────────

    def _handle_command(self, data: dict):
        target    = data.get('target')
        window_id = data.get('window_id', 0)
        win       = self.windows.get(window_id)

        if target == 'Show' and win:
            win.show_asset(
                src      = data.get('src', ''),
                container= data.get('container', 0),
                fit      = data.get('fit', 0),
                bg_color = data.get('bg_color', 'rgb(0,0,0)'),
            )
        elif target == 'SetBounds' and win:
            win.set_bounds(data['x'], data['y'], data['width'], data['height'])
        elif target == 'SetOrientation' and win:
            win.set_orientation(data.get('orientation', 0))
        elif target == 'SetFlip' and win:
            win.set_flip(data.get('flip', 0))
        elif target == 'AddWindow':
            self._open_window(
                screen_index=data.get('display', 0),
                x=data.get('x', 0), y=data.get('y', 0),
                width=data.get('width', 0), height=data.get('height', 0),
            )
        elif target == 'RemoveWindow' and win:
            win.close()
            del self.windows[window_id]
