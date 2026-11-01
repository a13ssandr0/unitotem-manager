"""
One signal for "what the windows show may have changed": a playlist was edited or created or deleted, a window was assigned another playlist, a layout was
saved. The places that make those changes call `changed()`; whoever depends on the result (the remote control, which keeps the files its clients need
sent to them) has registered with `listen()`. Callbacks must be quick and are called in the thread that made the change, so they only schedule work.
"""
from typing import Callable

from loguru import logger

_listeners: list[Callable[[], None]] = []


def listen(callback: Callable[[], None]):
    _listeners.append(callback)


def changed():
    for callback in list(_listeners):
        try:
            callback()
        except Exception:
            logger.exception('A listener of content changes failed')
