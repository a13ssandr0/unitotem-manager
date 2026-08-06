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
import subprocess
from base64 import b64decode, b64encode
from hashlib import sha256
from typing import Optional

from loguru import logger
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication

from utils import constants as const
from utils.browser import user_agent
from utils.models.viewer import viewer_settings

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
    # Asks the Qt main thread to quit exec() (see WebviewApp.request_quit):
    # emitted from the asyncio thread on a real shutdown, since with
    # quitOnLastWindowClosed disabled nothing else makes exec() return.
    quit_requested = Signal()


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

    @staticmethod
    def _own_cert_spki_hash(certfile: str) -> Optional[str]:
        """
        base64(sha256(SubjectPublicKeyInfo)) of this node's own certificate, in
        the form Chromium's --ignore-certificate-errors-spki-list expects.

        The manager's internal pages (the first-boot welcome screen and the
        "no assets" placeholder, utils/models/assets.py) are served by this
        very node over https with a self-signed certificate, which CEF
        validates like any other site and rejects - so a device would show
        Chromium's grey error page exactly when the welcome screen is the only
        thing its user can see.

        Pinning that one public key is the narrowest fix that actually works
        here. RequestHandler.OnCertificateError is not an option despite being
        the obvious candidate: these pages are loaded in an *iframe* by
        boot-screen.html, and Chromium refuses certificate errors on subframes
        outright instead of consulting the handler (verified - the callback is
        never invoked). CefSettings.ignore_certificate_errors would have worked
        too, but it disables validation for scheduled remote assets as well.

        Returns None if the certificate cannot be read, in which case nothing
        is pinned and behaviour is unchanged.
        """
        try:
            der = subprocess.run(
                ['openssl', 'x509', '-in', certfile, '-pubkey', '-noout'],
                capture_output=True, check=True).stdout
            spki = subprocess.run(
                ['openssl', 'pkey', '-pubin', '-outform', 'der'],
                input=der, capture_output=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning('Cannot read own certificate {} ({}); the local '
                           'welcome/no-assets pages will fail to load', certfile, exc)
            return None
        return b64encode(sha256(spki).digest()).decode()

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
        switches = {
            'no-proxy-server'  : '',
            'disable-extensions': '',
            # Allow videos and audio to auto-play without user interaction
            'autoplay-policy'  : 'no-user-gesture-required',
            'remote-debugging-address': '127.0.0.1',
            # boot-screen.html is loaded over file://, so every asset it shows
            # is cross-origin to it and the same-origin policy gets in the way
            # of the viewer's own job: drawing a scheduled image into a canvas
            # taints it, which is what stops the average-colour backdrop from
            # being computed. This browser only ever displays what the operator
            # scheduled, in a kiosk with no address bar, no user input and no
            # credentials to steal cross-origin, so the policy protects nothing
            # here while breaking a feature. Chromium requires a dedicated
            # user-data-dir for this, which cache_path above already provides.
            'disable-web-security': '',
            'allow-file-access-from-files': '',
        }
        # See _own_cert_spki_hash(): trust this node's own certificate, and
        # only that one, so its locally served pages render instead of an error.
        spki = self._own_cert_spki_hash(const.certfile)
        if spki:
            switches['ignore-certificate-errors-spki-list'] = spki

        # Opt-in, off by default (Settings/Display/setAllowInsecureCerts):
        # show scheduled assets even when their certificate does not validate.
        # This is deliberately the blunt Chromium switch and not
        # RequestHandler.OnCertificateError - see _own_cert_spki_hash(), the
        # handler is never consulted for the iframe the assets live in. The
        # CefSettings equivalent no longer exists either: this CEF version
        # removed the ignore_certificate_errors application setting (see
        # vendor/cefpython/docs/Migration-guide.md). Being an initialisation
        # switch, it only takes effect on a viewer restart, which is what the
        # web UI tells the operator.
        if viewer_settings.allow_insecure_certs:
            logger.warning('Certificate validation is disabled for scheduled assets: '
                           'anything able to intercept the connection can choose '
                           'what this node displays')
            switches['ignore-certificate-errors'] = ''

        cef.Initialize(
            settings={
                'windowless_rendering_enabled' : False,
                'multi_threaded_message_loop'  : False,
                'log_severity'                 : cef.LOGSEVERITY_WARNING,
                'remote_debugging_port'        : CDP_PORT,
                'cache_path'                   : cache_path,
                # Paint black, not white, before a document has loaded. CEF's
                # documented default for a windowed browser is opaque white
                # (see vendor/cefpython/api/ApplicationSettings.md), which on a
                # kiosk shows up as a full-screen white flash every time a
                # window is created. 32-bit ARGB, alpha must be fully opaque.
                'background_color'             : 0xFF000000,
                # Same identity the backend's media type probe uses, so what it
                # is told about an asset is what this browser would be told.
                'user_agent'                   : user_agent(),
            },
            switches=switches,
        )

        self.qt_app = QApplication.instance() or QApplication([])
        self.qt_app.setApplicationName('UniTotem')

        # A UniTotem node must keep running with zero screens - at boot and
        # after the *last* monitor is unplugged at runtime. Qt's default
        # quitOnLastWindowClosed=True would make exec() return the moment the
        # last window closes (screenRemoved -> _on_screen_removed -> close()),
        # which via aboutToQuit -> _schedule_shutdown() tears down the whole
        # manager. Decouple app lifetime from window count; the app instead
        # exits only on an explicit request_quit() (real shutdown), and picks
        # screens back up via screenAdded -> _open_window_for_screen.
        self.qt_app.setQuitOnLastWindowClosed(False)

        self._bridge = _Bridge()
        self._bridge.command_received.connect(self._handle_command)
        self._bridge.quit_requested.connect(self.qt_app.quit)

        self.windows: dict[int, WebviewWindow] = {}
        self._screen_windows: dict[QScreen, int] = {}
        self._next_id = 0
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None

        # One fullscreen window per currently-connected screen (zero screens
        # means zero windows: the app must be able to start without any and
        # keep running, picking up screens as they're connected below).
        for screen in self._real_screens():
            self._open_window_for_screen(screen)
        self.qt_app.screenAdded.connect(self._open_window_for_screen)
        self.qt_app.screenRemoved.connect(self._on_screen_removed)

    # ── window management ─────────────────────────────────────────────────

    @staticmethod
    def _is_placeholder(screen: QScreen) -> bool:
        """
        Whether this is Qt's stand-in for "no screen at all" rather than a
        real output.

        QGuiApplication always keeps at least one QScreen, so with every
        output disconnected the xcb backend leaves behind a placeholder named
        after the X display itself (':0.0') instead of after a RandR output.
        Observed transitions on the test VM:

          0 outputs            -> one screen  ':0.0'      0x0
          first output plugged -> screenAdded 'Virtual-1', then
                                  screenRemoved ':0.0'
          last output unplugged-> the existing QScreen is *renamed in place*
                                  to ':0.0', keeping its now-stale geometry,
                                  and NO signal of any kind is emitted

        Without this check the manager opens a window on the placeholder when
        it starts with no monitor attached, and reports a screen that is not
        there. Real outputs are named after their RandR output (HDMI-1,
        Virtual-1, ...), which never begins with ':'; the geometry test covers
        the freshly-created placeholder, whose size is 0x0.

        This does NOT cover the last-output-unplugged case: in a process that
        owns a window on that screen, Qt keeps the QScreen under its original
        name and geometry indefinitely (measured: still 'Virtual-1' 1280x800
        more than 20s after the output was gone). Detecting that needs a RandR
        event source of our own, independent of Qt - see TODO.
        """
        return screen.name().startswith(':') or screen.geometry().isEmpty()

    def _real_screens(self) -> list[QScreen]:
        """Connected outputs only, with Qt's placeholder filtered out."""
        return [s for s in self.qt_app.screens() if not self._is_placeholder(s)]

    def _open_window_for_screen(self, screen: QScreen) -> int:
        """Open a fullscreen window on a newly-detected screen. No playlist
        is assigned automatically (assignment is a separate, explicit step)."""
        if self._is_placeholder(screen):
            # Not a real output - see _is_placeholder(). Nothing to show it on.
            return -1
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
        self._notify_screens_changed()
        return self._next_id - 1

    def _on_screen_removed(self, screen: QScreen):
        """Close the window associated with a screen that just disconnected."""
        window_id = self._screen_windows.pop(screen, None)
        if window_id is not None:
            win = self.windows.pop(window_id, None)
            if win is not None:
                win.close()
        self._notify_screens_changed()

    def _notify_screens_changed(self):
        """
        Push the current screen/window layout to the manager immediately
        instead of waiting for the next reconnect, so a monitor plugged or
        unplugged at runtime (screenAdded/screenRemoved, fired on this - the
        Qt - thread) shows up on the Viewers page right away. The WS
        connection itself lives on a different thread/event loop (see
        run()/_ws_client()), so the actual send has to be scheduled onto that
        loop rather than awaited directly here.
        """
        if self._ws_loop is not None and self._ws is not None:
            asyncio.run_coroutine_threadsafe(self._send_webview_info(self._ws), self._ws_loop)

    def request_quit(self):
        """
        Thread-safe: ask the Qt main thread's exec() to return. Called from
        main.py's _schedule_shutdown() (asyncio thread) on a real shutdown -
        with quitOnLastWindowClosed disabled, this is the only way exec()
        ever returns, so run() can reach cef.Shutdown() and the process can
        exit cleanly instead of hanging on SIGTERM.
        """
        self._bridge.quit_requested.emit()

    def _open_window(self, screen_index: int = 0,
                     x: int = 0, y: int = 0,
                     width: int = 0, height: int = 0) -> int:
        """Open an extra window on an already-connected screen, at a
        possibly custom size (used by the remote 'AddWindow' command)."""
        screens = self._real_screens()
        if not screens:
            # AddWindow with no screens connected: log and no-op rather than
            # raising, since this runs inside a Qt slot (_handle_command) -
            # an unhandled exception there can abort the whole process.
            logger.warning('AddWindow requested but no screens are connected; ignoring')
            return -1
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
        self._notify_screens_changed()
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
        # 10ms, as in the cefpython examples. Raising it does NOT save CPU here:
        # measured on the test VM with static content, the main thread sits at
        # 84% of a core at 10ms and 85% at 50ms - five times fewer ticks, no
        # difference. Whatever is spinning is the Qt event loop itself, not how
        # often CEF's queue is drained, so a longer interval would only cost
        # input latency for nothing. Leave it alone until the real cause is
        # found. Not the way out either: external_message_pump and
        # multi_threaded_message_loop, both dead ends on Linux (see CLAUDE.md).
        cef_timer.start(10)

        # cef.MessageLoopWork is a C-extension function: PySide6 can invoke it
        # as a slot without ever entering CPython's bytecode eval loop, which
        # is the only place pending signals (SIGTERM/SIGINT, e.g. from
        # request_quit's caller or systemd) get checked and their Python
        # handler actually run. Without some genuine Python-level callable
        # firing periodically, a signal can stay pending indefinitely while
        # this thread is otherwise fully occupied by native Qt/CEF calls -
        # verified empirically: this single addition took a SIGTERM-to-exit
        # delay from indefinite (previously masked by systemd's 90s
        # TimeoutStopSec forcing a SIGKILL) down to about a second.
        sigcheck_timer = QTimer()
        sigcheck_timer.timeout.connect(lambda: None)
        sigcheck_timer.start(200)

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
                    self._ws = ws
                    await self._send_webview_info(ws)
                    async for raw in ws:
                        try:
                            self._bridge.command_received.emit(self._decode_frame(raw))
                        except Exception:
                            pass
            except Exception:
                # Retry until the manager is up (e.g. still starting)
                await asyncio.sleep(3)
            finally:
                self._ws = None

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
        for i, s in enumerate(self._real_screens()):
            g = s.geometry()
            screens.append({
                'index': i, 'name': s.name(),
                'x': g.x(), 'y': g.y(),
                'width': g.width(), 'height': g.height(),
            })
        # window_id → screen name, so the web UI can label a window by the
        # output it occupies ('HDMI-1') instead of by a bare number. Only known
        # here, where windows are created against a QScreen.
        window_screen = {wid: screen.name() for screen, wid in self._screen_windows.items()}
        windows = []
        for wid, win in self.windows.items():
            g = win.geometry()
            windows.append({
                'window_id': wid,
                'screen_name': window_screen.get(wid),
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
            self._notify_screens_changed()
