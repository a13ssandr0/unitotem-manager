"""
The screen's turn in the kernel command line, so that Plymouth is turned from the initramfs on: the image's root helper
`unitotem-boot-orientation` (get/set, option `panel_orientation` of the connector's `video=` token; effective at the next boot).
Plymouth cannot mirror, so only the quarter turns are given.
"""
import json
import shutil
from typing import Optional

from loguru import logger

from .screens._exec import ToolMissing, ToolTimeout, run

HELPER = 'unitotem-boot-orientation'
# quarter turns clockwise of the content -> the helper's names (right = right_side_up, left = left_side_up)
NAMES = {0: 'normal', 1: 'right', 2: 'inverted', 3: 'left'}


async def current(connector: str) -> Optional[str]:
    """What the command line says now, None when the helper is not on this image or does not answer."""
    binary = shutil.which(HELPER)
    if binary is None:
        return None
    try:
        result = await run([binary, 'get', connector], timeout=15)
        return json.loads(result.stdout)['orientation'] if result.returncode == 0 else None
    except (ToolMissing, ToolTimeout, ValueError, KeyError, TypeError):
        return None


async def set_turn(connector: str, turn: int) -> bool:
    """Make the kernel command line turn this connector `turn` quarter turns; only written when it differs (the boot partition is
    flash). False when nothing was done or it failed: the screen itself is turned by the compositor either way."""
    binary = shutil.which(HELPER)
    if binary is None:
        logger.debug('{} is not on this image: Plymouth keeps its orientation', HELPER)
        return False
    wanted = NAMES[int(turn) % 4]
    if await current(connector) == wanted:
        return False
    try:
        result = await run([binary, 'set', connector, wanted], timeout=30)
    except (ToolMissing, ToolTimeout) as error:
        logger.warning('{} {} {} failed: {}', HELPER, connector, wanted, error)
        return False
    if result.returncode != 0:
        logger.warning('{} {} {} failed: {}', HELPER, connector, wanted, (result.stdout or result.stderr).strip()[:200])
        return False
    return True
