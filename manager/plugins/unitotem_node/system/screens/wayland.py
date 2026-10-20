"""
The compositor side of screen control: which outputs exist, and switching one off and on.

`wlr-randr --output X --off` / `--on` (the wlr-output-management protocol, which labwc speaks) is the primary mechanism
because it needs no DDC and no CEC and works per output; the power management protocol (`wlopm`, DPMS in the old
vocabulary) is the fallback for a node whose outputs cannot be disabled, and the way to blank everything at once. Switching an
output off makes the viewer close that output's window and switching it on opens a new one; the playlist assignment is keyed on
the output name, so it follows (see webview/controller.py).

Both tools talk to the compositor through $WAYLAND_DISPLAY / $XDG_RUNTIME_DIR; without a compositor they fail and the
functions answer "nothing there".
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from ._exec import run
from .identity import edid_identity

# wlr-randr's transform names and the names the web UI has always used for the same rotations (xrandr's)
_TRANSFORM_TO_ROTATION = {'normal': 'normal', '90': 'left', '180': 'inverted', '270': 'right',
                          'flipped': 'normal', 'flipped-90': 'left', 'flipped-180': 'inverted', 'flipped-270': 'right'}
_ROTATION_TO_TRANSFORM = {'normal': 'normal', 'left': '90', 'inverted': '180', 'right': '270'}


@dataclass(frozen=True)
class Output:
    name: str
    connected: bool
    active: bool          # enabled, i.e. being driven (an output that is connected but off is not)
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    rotation: str = 'normal'
    make: str = ''
    model: str = ''
    serial: str = ''


def parse_outputs(text: str) -> list[Output]:
    """The outputs in `wlr-randr --json`. The compositor only lists outputs that are connected."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    found = []
    for entry in data if isinstance(data, list) else []:
        mode = next((m for m in entry.get('modes', []) if m.get('current')), None)
        enabled = bool(entry.get('enabled')) and mode is not None
        position = entry.get('position') or {}
        transform = entry.get('transform') or 'normal'
        width, height = (mode['width'], mode['height']) if mode else (0, 0)
        if enabled and transform in ('90', '270', 'flipped-90', 'flipped-270'):
            width, height = height, width      # the rectangle the output covers in the layout is the rotated one
        found.append(Output(
            name=entry['name'], connected=True, active=enabled, width=width, height=height,
            x=int(position.get('x', 0)), y=int(position.get('y', 0)),
            rotation=_TRANSFORM_TO_ROTATION.get(transform, 'normal'),
            make=entry.get('make') or '', model=entry.get('model') or '', serial=entry.get('serial') or ''))
    return found


async def outputs() -> list[Output]:
    """Connected outputs with their current state. Empty when there is no compositor to ask (a headless node)."""
    try:
        result = await run(['wlr-randr', '--json'], timeout=10)
    except Exception as exc:
        logger.debug('wlr-randr unavailable: {}', exc)
        return []
    if result.returncode != 0:
        return []
    return parse_outputs(result.stdout)


def read_edid(name: str, root: str = '/sys/class/drm') -> Optional[bytes]:
    """The EDID of an output, from the kernel: the DRM connector has the output's own name (`card0-DP-2`)."""
    for connector in sorted(Path(root).glob(f'card*-{name}')):
        try:
            data = (connector / 'edid').read_bytes()
        except OSError:
            continue
        if data:
            return data
    return None


async def identities() -> dict[str, str]:
    """Output name -> 'MFG:Model:Serial', for the outputs whose EDID the kernel exposes."""
    found = {}
    for output in await outputs():
        edid = await asyncio.to_thread(read_edid, output.name)
        identity = edid_identity(edid) if edid else None
        if identity:
            found[output.name] = identity
    return found


# Where an output was before it was switched off, so switching it on puts it back
_last_geometry: dict[str, Output] = {}


async def set_power(name: str, on: bool, known: Optional[Output] = None):
    """Switch one output off or on. Raises RuntimeError with wlr-randr's own message when it refuses."""
    if on:
        cmd = ['wlr-randr', '--output', name, '--on']
        before = _last_geometry.get(name)
        if before is not None:
            cmd += ['--pos', f'{before.x},{before.y}', '--transform', _ROTATION_TO_TRANSFORM.get(before.rotation, 'normal')]
    else:
        if known is not None and known.active:
            _last_geometry[name] = known
        cmd = ['wlr-randr', '--output', name, '--off']
    result = await run(cmd, timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'wlr-randr exited with {result.returncode}')
    if on:
        await _power_protocol(name, True)       # an output blanked by the power protocol is not woken by being enabled
    else:
        await asyncio.sleep(0.5)
        now = next((o for o in await outputs() if o.name == name), None)
        if now is not None and now.active:
            # the compositor keeps its last output enabled (the Raspberry Pi's, for one): blank it instead
            await _power_protocol(name, False)


async def _power_protocol(name: str, on: bool):
    try:
        await dpms(on, name)
    except RuntimeError as exc:
        logger.debug('The power protocol did not take {}: {}', name, exc)


async def dpms(on: bool, name: str = '*'):
    """Blank or wake every output (or one) with the output power management protocol."""
    result = await run(['wlopm', '--on' if on else '--off', name], timeout=10)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'wlopm exited with {result.returncode}')


async def wake_all():
    """Policy at start (and after a resume): nothing blanks by itself, outputs are switched by timers and commands only
    (labwc has no idle blanking of its own), and whatever a previous session left blanked is woken."""
    await dpms(True)
