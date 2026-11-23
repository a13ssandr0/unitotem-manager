"""Runtime product version, read from the image's os-release style file.

The file is /usr/share/unitotem/unitotem-release (override: UNITOTEM_RELEASE_FILE).
Without it, the version comes from the installed unitotem-manager package.
"""
import functools
import importlib.metadata
import os
import re
from pathlib import Path

RELEASE_FILE = Path('/usr/share/unitotem/unitotem-release')
VERSION_RE = re.compile(r'[0-9]{4}\.[0-9]{2}(\.[0-9]+)?(~[a-z]+[0-9]+)?')
_DPKG_VERSION_RE = re.compile(r'([0-9]+(?:\.[0-9]+)*)(?:~([A-Za-z0-9.+]*))?')
_LINE_RE = re.compile(r'([A-Za-z0-9_]+)=(.*)')
_ESCAPE_RE = re.compile(r'\\([\\$"`])')
_DEVELOPMENT = 'development'


def _package_version(name):
    return importlib.metadata.version(name)


def _unquote(raw):
    # Shell quoting as in /etc/os-release: '...' is literal, "..." and bare text unescape \\ \$ \" \`.
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    return _ESCAPE_RE.sub(r'\1', raw)


def _parse(text):
    info = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = _LINE_RE.fullmatch(line)
        if match is not None:
            info[match.group(1)] = _unquote(match.group(2))
    return info


def _fallback_version():
    try:
        return _package_version('unitotem-manager')
    except importlib.metadata.PackageNotFoundError:
        return _DEVELOPMENT


@functools.cache
def _load():
    override = os.environ.get('UNITOTEM_RELEASE_FILE')
    path = Path(override) if override else RELEASE_FILE
    try:
        info = _parse(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        info = None
    if info is None:
        v = _fallback_version()
        return {'NAME': 'UniTotem', 'ID': 'unitotem', 'VERSION_ID': v, 'VERSION': v, 'PRETTY_NAME': f'UniTotem {v}'}
    if not info.get('VERSION_ID'):
        v = _fallback_version()
        info.update(VERSION_ID=v, VERSION=v, PRETTY_NAME=f'UniTotem {v}')
    return info


def is_valid(v):
    return isinstance(v, str) and VERSION_RE.fullmatch(v) is not None


def info():
    return _load()


def version():
    return info()['VERSION_ID']


def pretty_name():
    return info()['PRETTY_NAME']


def reload():
    _load.cache_clear()


def _verrevcmp(a, b):
    # dpkg's comparison of non-numeric and numeric runs (lib/dpkg/version.c).
    def order(s, i):
        if i >= len(s):
            return 0
        c = s[i]
        if c.isdigit():
            return 0
        if c.isalpha():
            return ord(c)
        if c == '~':
            return -1
        return ord(c) + 256

    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac, bc = order(a, i), order(b, j)
            if ac != bc:
                return -1 if ac < bc else 1
            i += 1
            j += 1
        while i < len(a) and a[i] == '0':
            i += 1
        while j < len(b) and b[j] == '0':
            j += 1
        first_diff = 0
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return -1 if first_diff < 0 else 1
    return 0


def _split(v):
    if not isinstance(v, str):
        return None
    match = _DPKG_VERSION_RE.fullmatch(v)
    if match is None:
        return None
    nums = [int(part) for part in match.group(1).split('.')]
    while nums and nums[-1] == 0:
        nums.pop()
    return nums, match.group(2)


def version_at_least(version, minimum):
    a = _split(version)
    b = _split(minimum)
    if a is None or b is None:
        return False
    (na, ta), (nb, tb) = a, b
    size = max(len(na), len(nb))
    na = na + [0] * (size - len(na))
    nb = nb + [0] * (size - len(nb))
    if na != nb:
        return na > nb
    # Same base: a '~' suffix sorts before the same version without it.
    if ta is None:
        return True
    if tb is None:
        return False
    return _verrevcmp(ta, tb) >= 0
