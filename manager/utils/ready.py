"""
Tells systemd the service is ready (Type=notify), once.

What "ready" means depends on what the node shows: with local windows it is the first frame of the viewer on screen,
which is what ends the boot splash; without them it is the web server answering. The side that is responsible calls
`ready()`; the other one stays out of it (see main.py). Without systemd (a development run) nothing happens.
"""
import threading

from loguru import logger
from sdnotify import SystemdNotifier

_notifier = SystemdNotifier()
_lock = threading.Lock()
_sent = False


def ready(reason: str = ''):
    global _sent
    with _lock:
        if _sent:
            return
        _sent = True
    _notifier.notify('READY=1')
    logger.info('Service ready{}', f' ({reason})' if reason else '')
