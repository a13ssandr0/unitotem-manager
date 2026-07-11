"""
ViewerApp — Qt6+CEF viewer integrated into the UniTotem manager process.

Architecture
───────────
  Main thread  : Qt event loop + CEF message pump (QTimer @ 10 ms)
  asyncio thread: WebSocket client that receives manager commands

Commands from the WS thread are forwarded to Qt via Signal/Slot,
which is the only thread-safe bridge between the two event loops.
"""
import asyncio
import json
import os
import socket
import ssl
import threading
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .window import ViewerWindow

try:
    from cefpython3 import cefpython as cef
except ImportError:
    cef = None


class _Bridge(QObject):
    """Carries commands from the asyncio/WS thread to the Qt main thread."""
    command_received = Signal(dict)


class ViewerApp:
    """
    Initialise and run the local Qt6+CEF viewer.

    Parameters
    ----------
    manager_url  : WebSocket URL of the manager's /remote endpoint
                   (e.g. 'wss://localhost:443/remote')
    instance_id  : Unique identifier for this viewer instance
    """

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

        cef.Initialize(
            settings={
                'windowless_rendering_enabled' : False,
                'multi_threaded_message_loop'  : False,
                'log_severity'                 : cef.LOGSEVERITY_WARNING,
                'remote_debugging_port'        : 0,
            },
            switches={
                'no-proxy-server'  : '',
                'disable-extensions': '',
                # Allow videos and audio to auto-play without user interaction
                'autoplay-policy'  : 'no-user-gesture-required',
            },
        )

        self.qt_app = QApplication.instance() or QApplication([])
        self.qt_app.setApplicationName('UniTotem')

        self._bridge = _Bridge()
        self._bridge.command_received.connect(self._handle_command)

        self.windows: dict[int, ViewerWindow] = {}
        self._next_id = 0
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None

        # Fullscreen window on the primary screen
        self._open_window()

    # ── window management ─────────────────────────────────────────────────

    def _open_window(self, screen_index: int = 0,
                     x: int = 0, y: int = 0,
                     width: int = 0, height: int = 0) -> int:
        screens = self.qt_app.screens()
        screen  = screens[min(screen_index, len(screens) - 1)]
        geom    = screen.geometry()
        win = ViewerWindow(
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
            target=self._ws_thread_main, name='viewer-ws', daemon=True
        )
        ws_thread.start()

        # Drive CEF's internal message loop from a Qt timer
        cef_timer = QTimer()
        cef_timer.timeout.connect(cef.MessageLoopWork)
        cef_timer.start(10)

        exit_code = self.qt_app.exec()
        cef_timer.stop()
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
                    await self._send_viewer_info(ws)
                    async for raw in ws:
                        try:
                            self._bridge.command_received.emit(json.loads(raw))
                        except Exception:
                            pass
            except Exception:
                # Retry until the manager is up (e.g. still starting)
                await asyncio.sleep(3)

    async def _send_viewer_info(self, ws):
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
            'target' : 'ViewerInfo',
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
