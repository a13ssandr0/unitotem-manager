"""
Keeps the first-access state in step with the system's accounts.

The first-access window (root in the group unitotem-first-admin) closes once an administrator exists. An administrator can
appear outside the web interface too (a `gpasswd` run on the machine, a restored backup), so start-up checks the state
once, and then the group files of /etc are watched: a change to `group` or `gshadow` is checked again after a short
debounce. Events, not polling.
"""
import ctypes
import os
import struct
from typing import Optional

from loguru import logger

from utils import first_access
from utils.models.user import user_manager
from webview.controller import WebviewManager

WATCHED_DIR = '/etc'
WATCHED_NAMES = {'group', 'gshadow'}
DEBOUNCE = 1.0                                              # seconds after the last change before checking
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
_EVENT = struct.Struct('iIII')                              # wd, mask, cookie, name length (then the name)


def _inotify(path: str) -> Optional[int]:
    """A non-blocking inotify descriptor watching the entries of `path` that are written or renamed in (None when it
    cannot be set up)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            return None
        if libc.inotify_add_watch(fd, path.encode(), IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE) < 0:
            os.close(fd)
            return None
        return fd
    except (OSError, AttributeError):
        return None


def _reconcile():
    try:
        first_access.reconcile()
    except Exception as e:
        logger.error('Cannot close the first-access setup: {}', e)


def start(loop):
    """Closes a leftover first-access window and watches the accounts for an administrator added from outside."""
    _reconcile()
    fd = _inotify(WATCHED_DIR)
    if fd is None:
        logger.warning('Cannot watch {} for changes of the accounts: the first-access setup is checked at start-up only',
                       WATCHED_DIR)
        return

    state = {'had_admin': user_manager.has_admin(), 'timer': None}

    def check():
        state['timer'] = None
        try:
            has_admin = user_manager.has_admin()
        except Exception as e:
            logger.error('Cannot read the administrators: {}', e)
            return
        if has_admin == state['had_admin']:
            return
        state['had_admin'] = has_admin
        if has_admin:
            logger.info('An administrator exists now')
            _reconcile()
            if WebviewManager._instance is not None:
                WebviewManager._instance.refresh_idle()     # the welcome screen of the totem gives way to the placeholder

    def readable():
        relevant = False
        try:
            while data := os.read(fd, 4096):
                offset = 0
                while offset + _EVENT.size <= len(data):
                    _wd, _mask, _cookie, length = _EVENT.unpack_from(data, offset)
                    name = data[offset + _EVENT.size:offset + _EVENT.size + length].rstrip(b'\0').decode(errors='replace')
                    relevant = relevant or name in WATCHED_NAMES
                    offset += _EVENT.size + length
        except BlockingIOError:
            pass
        except OSError as e:
            logger.error('Lost the watch on {}: {}', WATCHED_DIR, e)
            loop.remove_reader(fd)
            os.close(fd)
            return
        if relevant:
            if state['timer'] is not None:
                state['timer'].cancel()
            state['timer'] = loop.call_later(DEBOUNCE, check)

    loop.add_reader(fd, readable)
