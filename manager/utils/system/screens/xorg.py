"""
The X side of screen control: which outputs exist, and switching one off and on with RandR.

`xrandr --output X --off` / `--auto` is the primary mechanism because it needs no DDC and no CEC and works per
output (DPMS is per X screen, so it is only the fallback for single-output nodes). Switching an output off makes
the viewer close that output's window and switching it on opens a new one; the playlist assignment is keyed on the
output name, so it follows (see webview/controller.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from ._exec import run
from .identity import edid_identity, xrandr_edid_property

_OUTPUT_RE = re.compile(
    r'^(?P<name>\S+) (?P<state>connected|disconnected)(?: primary)?'
    r'(?: (?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+))?(?: (?P<rotation>left|right|inverted|normal))?')


@dataclass(frozen=True)
class XOutput:
    name: str
    connected: bool
    active: bool          # has a mode set, i.e. is being driven (an output that is connected but off is not)
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    rotation: str = 'normal'


def parse_xrandr_query(text: str) -> list[XOutput]:
    """The outputs in `xrandr --query` (or `--props`) output."""
    out = []
    for line in text.splitlines():
        m = _OUTPUT_RE.match(line)
        if not m:
            continue
        connected = m['state'] == 'connected'
        active = m['w'] is not None
        out.append(XOutput(
            name=m['name'], connected=connected, active=active,
            width=int(m['w'] or 0), height=int(m['h'] or 0), x=int(m['x'] or 0), y=int(m['y'] or 0),
            rotation=m['rotation'] or 'normal'))
    return out


async def outputs() -> list[XOutput]:
    """Connected outputs with their current state. Empty when there is no X server to ask (a headless node)."""
    try:
        result = await run(['xrandr', '--query'], timeout=10)
    except Exception as exc:
        logger.debug('xrandr unavailable: {}', exc)
        return []
    if result.returncode != 0:
        return []
    return [o for o in parse_xrandr_query(result.stdout) if o.connected]


async def identities() -> dict[str, str]:
    """RandR output name -> 'MFG:Model:Serial', for the outputs whose EDID xrandr exposes."""
    try:
        result = await run(['xrandr', '--props'], timeout=10)
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    found = {}
    for output in parse_xrandr_query(result.stdout):
        if not output.connected:
            continue
        edid = xrandr_edid_property(result.stdout, output.name)
        identity = edid_identity(edid) if edid else None
        if identity:
            found[output.name] = identity
    return found


# Where an output was before it was switched off, so switching it on puts it back
_last_geometry: dict[str, XOutput] = {}


async def set_power(name: str, on: bool, known: Optional[XOutput] = None):
    """Switch one output off or on. Raises RuntimeError with xrandr's own message when it refuses."""
    if on:
        cmd = ['xrandr', '--output', name, '--auto']
        before = _last_geometry.get(name)
        if before is not None:
            cmd += ['--pos', f'{before.x}x{before.y}']
            if before.rotation != 'normal':
                cmd += ['--rotate', before.rotation]
    else:
        if known is not None and known.active:
            _last_geometry[name] = known
        cmd = ['xrandr', '--output', name, '--off']
    result = await run(cmd, timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'xrandr exited with {result.returncode}')


async def dpms(on: bool):
    """Blank or wake the whole X screen. Zero timeouts first so no automatic blanking timer is ever armed."""
    for cmd in (['xset', '+dpms'], ['xset', 'dpms', '0', '0', '0'], ['xset', 'dpms', 'force', 'on' if on else 'off']):
        result = await run(cmd, timeout=5)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or 'xset failed')
