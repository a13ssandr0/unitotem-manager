"""
The compositor of the kiosk is labwc, and a part of its configuration is written by the program at run time: the node maps each
touch screen to an output (unitotem_node/system/touch.py). labwc is started with `--merge-config` and an XDG_CONFIG_DIRS that lists
one directory per writer, so each package owns a file nobody else touches:

    $UNITOTEM_LABWC_RUN (default /run/unitotem/labwc)/<name>/labwc/rc.xml

and re-reads all of them on SIGHUP. Whoever writes a fragment calls `reload()`. Everything here is harmless without labwc
(a development machine, a desktop compositor): `reload()` finds nobody to signal and the caller carries on.
"""
import os
import signal
from pathlib import Path
from typing import Optional

import psutil
from loguru import logger

RUN_DIR = Path(os.environ.get('UNITOTEM_LABWC_RUN', '/run/unitotem/labwc'))
EMPTY = '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n'


def fragment_path(name: str) -> Path:
    return RUN_DIR / name / 'labwc' / 'rc.xml'


def config_dirs(names=('node',)) -> str:
    """The value for XDG_CONFIG_DIRS of the compositor, besides the base configuration of the image."""
    return ':'.join(str(RUN_DIR / name) for name in names)


def pid() -> Optional[int]:
    """The compositor's process: labwc exports LABWC_PID to what it starts itself, a service of its own looks for it."""
    if os.environ.get('LABWC_PID', '').isdigit():
        return int(os.environ['LABWC_PID'])
    for process in psutil.process_iter(['name']):
        if process.info['name'] == 'labwc':
            return process.pid
    return None


def write_fragment(name: str, body: str) -> bool:
    """Writes `<labwc_config>body</labwc_config>` as the fragment of `name`, atomically. False if it cannot be written."""
    path = fragment_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(f'<?xml version="1.0"?>\n<labwc_config>\n{body}</labwc_config>\n')
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.warning('Cannot write the compositor configuration {}: {}', path, exc)
        return False


def ensure_fragments(names=('node',)):
    """An empty, valid fragment for every writer, so that the compositor can start before any of them has run."""
    for name in names:
        path = fragment_path(name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(EMPTY)


def reload() -> bool:
    """Asks labwc to read its configuration again. True when a compositor was signalled."""
    target = pid()
    if target is None:
        return False
    try:
        os.kill(target, signal.SIGHUP)
        return True
    except OSError as exc:
        logger.warning('Cannot signal the compositor: {}', exc)
        return False
