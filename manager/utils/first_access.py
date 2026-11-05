"""
The first administrator of a device, and the end of the first-access window.

The web /setup page and the setup script both create the first administrator through create_first_admin(), under one
lock, so two of them cannot both succeed.
"""
import contextlib
import fcntl
import os
import time
from typing import Iterator

from loguru import logger

from utils import osusers

LOCK_PATH = '/run/unitotem/first-admin.lock'   # read at call time: tests override it
LOCK_TIMEOUT = 30.0
LOCK_RETRY = 0.1


class FirstAccessDone(Exception):
    """An administrator already exists."""


class FirstAccessBusy(Exception):
    """Another process holds the lock for too long."""


@contextlib.contextmanager
def first_admin_lock() -> Iterator[None]:
    os.makedirs(os.path.dirname(LOCK_PATH), mode=0o700, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FirstAccessBusy('Another setup is running') from None
                time.sleep(LOCK_RETRY)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def create_first_admin(name: str, password: str):
    """Creates the first administrator and closes the first-access window. Raises FirstAccessDone if one exists."""
    with first_admin_lock():
        if osusers.admin_exists():
            raise FirstAccessDone()
        osusers.create_user(name, password, admin=True)
        osusers.close_first_access()
    logger.info('First administrator {} created', name)


def reconcile():
    """Closes the first-access window when an administrator exists but the window is still open."""
    with first_admin_lock():
        if osusers.admin_exists() and osusers.first_access_open():
            osusers.close_first_access()
