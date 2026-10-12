"""
The small /proc and /sys helpers the sensor and CPU code needs.

They used to be imported from psutil's private modules (`psutil._common`, `psutil._ntuples`, `psutil._pslinux`), whose
contents move between releases: the same import worked with the pip version and failed on Debian's. Nothing here depends
on psutil's internals.
"""
import os
from collections import namedtuple

from loguru import logger

import psutil

_MISSING = object()

# One hardware temperature reading, and the CPU times of /proc/stat on Linux (the same fields psutil uses)
shwtemp = namedtuple('shwtemp', ['label', 'current', 'high', 'critical'])
scputimes = namedtuple('scputimes', ['user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal', 'guest',
                                     'guest_nice'])

CLOCK_TICKS = os.sysconf('SC_CLK_TCK')


def get_procfs_path() -> str:
    return psutil.PROCFS_PATH


def open_binary(path):
    return open(path, 'rb')


def cat(path, fallback=_MISSING, encoding='utf-8') -> str:
    """The text of a file, or `fallback` when it cannot be read (and no fallback means the error is raised)."""
    try:
        with open(path, encoding=encoding) as file:
            return file.read()
    except (OSError, ValueError):
        if fallback is not _MISSING:
            return fallback
        raise


def bcat(path, fallback=_MISSING) -> bytes:
    """Same as `cat`, in bytes."""
    try:
        with open(path, 'rb') as file:
            return file.read()
    except (OSError, ValueError):
        if fallback is not _MISSING:
            return fallback
        raise


def debug(message):
    logger.debug('{}', message)
