"""The local fullscreen windows: one frameless Qt window per screen, each hosting a CEF browser."""
import time

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from utils.models.command_line import cmdargs
import utils.ready as ready
from utils.plugins import Plugin

_app = None
_quit_requested = False


def run_gui(on_quit) -> bool:
    """Runs the windows on the main thread until the program ends. False when they cannot run at all (the Qt or CEF
    libraries are missing), in which case the server just carries on without a local display."""
    global _app
    try:
        from .app import WebviewApp
    except ImportError as exc:
        logger.warning('Viewer unavailable ({}), running without a local display', exc)
        ready.ready('no viewer')
        return False

    # No display at all (a node with no monitor, a service with no access to a graphical session) is a supported,
    # permanent situation: keep running and wait for one to appear, since constructing WebviewApp with none aborts the
    # whole process natively (SIGABRT, uncatchable) rather than raising an exception.
    if not WebviewApp.display_available():
        logger.warning('No display reachable; running headless until one appears')
        ready.ready('no display')       # nothing will be drawn: do not hold the boot splash for it
        while not SHUTDOWN_EVENT.is_set() and not WebviewApp.display_available():
            time.sleep(5)
    if SHUTDOWN_EVENT.is_set():
        return True

    _app = WebviewApp(manager_url=f'wss://localhost:{cmdargs.port_secure}/remote', instance_id='local-webview')
    _app.qt_app.aboutToQuit.connect(lambda: on_quit(from_gui=True))   # Qt quit -> shutdown of the whole program
    logger.info('Starting the local viewer')
    _app.run()                                      # blocks until request_quit()
    if not _quit_requested:
        # Qt ends its loop by itself when the X server goes away. That is not a clean exit: the service must fail so that
        # systemd starts it again on the new X server
        raise RuntimeError('The display connection was lost')
    return True


def request_quit():
    global _quit_requested
    _quit_requested = True
    if _app is not None:
        _app.request_quit()


PLUGIN = Plugin(name='viewer', run_gui=run_gui, request_quit=request_quit)
