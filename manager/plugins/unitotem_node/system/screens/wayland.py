"""
The compositor side of screen control: which outputs exist, and switching one off and on.

`wlr-randr --output X --off` / `--on` (the wlr-output-management protocol, which labwc speaks) is the primary mechanism
because it needs no DDC and no CEC and works per output; the power management protocol (`wlopm`, DPMS in the old
vocabulary) is the fallback for a node whose outputs cannot be disabled, and the way to blank everything at once. Switching an
output off makes the viewer close that output's window and switching it on opens a new one; the playlist assignment is keyed on
the output name, so it follows (see webview/controller.py).

Both tools talk to the compositor through $WAYLAND_DISPLAY (the absolute path of the socket); without a compositor they fail and the
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

# What turning and mirroring the CONTENT of a window in the page (quarter turns clockwise, flip 1 horizontal / 2 vertical) looks like
# when the compositor does it to the output instead: (turn, flip) -> wlr-randr transform. Compared picture by picture on a board
# (docs/rotation-benchmark.md): all twelve are identical.
CONTENT_TO_TRANSFORM = {
    (0, 0): 'normal', (0, 1): 'flipped', (0, 2): 'flipped-180',
    (1, 0): '270', (1, 1): 'flipped-270', (1, 2): 'flipped-90',
    (2, 0): '180', (2, 1): 'flipped-180', (2, 2): 'flipped',
    (3, 0): '90', (3, 1): 'flipped-90', (3, 2): 'flipped-270',
}
TRANSFORMS = frozenset(CONTENT_TO_TRANSFORM.values())     # the eight there are: a horizontal mirror turned twice is a vertical one


def transform_for(turn: int, flip: int) -> str:
    """The transform that shows what the page would show with `turn` quarter turns and `flip` (anything else counts as none)."""
    return CONTENT_TO_TRANSFORM[(int(turn) % 4, flip if flip in (1, 2) else 0)]


@dataclass(frozen=True)
class Output:
    name: str
    connected: bool
    active: bool          # shows something: enabled and not blanked (an output that is connected but off is not)
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    rotation: str = 'normal'
    transform: str = 'normal'     # wlr-randr's own name, mirror included (`rotation` has no mirror)
    make: str = ''
    model: str = ''
    serial: str = ''
    enabled: bool = True          # driven by the compositor (blanking by power management leaves it enabled)


def parse_outputs(text: str, blanked: frozenset = frozenset()) -> list[Output]:
    """The outputs in `wlr-randr --json`, `blanked` being the ones power management has switched off. The compositor only lists
    outputs that are connected."""
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
            name=entry['name'], connected=True, active=enabled and entry['name'] not in blanked, enabled=enabled, width=width, height=height,
            x=int(position.get('x', 0)), y=int(position.get('y', 0)),
            rotation=_TRANSFORM_TO_ROTATION.get(transform, 'normal'), transform=transform,
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
    return parse_outputs(result.stdout, await blanked_outputs())


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


async def set_transform(name: str, transform: str):
    """Turn and/or mirror one output. Raises RuntimeError with wlr-randr's own message when it refuses."""
    if transform not in TRANSFORMS:
        raise ValueError(f'Unknown transform {transform}')
    result = await run(['wlr-randr', '--output', name, '--transform', transform], timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'wlr-randr exited with {result.returncode}')


# Where an output was before it was switched off, so switching it on puts it back
_last_geometry: dict[str, Output] = {}


async def blanked_outputs() -> frozenset:
    """The outputs switched off by power management (`wlopm` prints `NAME on|off`), empty when the tool or protocol is missing."""
    try:
        result = await run(['wlopm'], timeout=10)
    except Exception:
        return frozenset()
    if result.returncode != 0:
        return frozenset()
    return frozenset(parts[0] for parts in (line.split() for line in result.stdout.splitlines()) if len(parts) == 2 and parts[1] == 'off')


async def set_power(name: str, on: bool, known: Optional[Output] = None):
    """Switch one output off or on. Power management (DPMS) first: the output stays enabled, so its window stays and the page is not
    reloaded when it comes back; only when that is refused the output itself is disabled. Raises RuntimeError with the
    compositor's message when nothing works."""
    if on:
        _kept_off.discard(name)
        if not _kept_off:
            _stop_uevents()
        try:
            await dpms(True, name)
        except RuntimeError as exc:
            logger.debug('The power protocol did not wake {}: {}', name, exc)
        current = next((o for o in await outputs() if o.name == name), None)
        if current is not None and current.enabled:
            return
        cmd = ['wlr-randr', '--output', name, '--on']
        before = _last_geometry.get(name)
        if before is not None:
            cmd += ['--pos', f'{before.x},{before.y}', '--transform', before.transform]
        result = await run(cmd, timeout=15)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or f'wlr-randr exited with {result.returncode}')
        await _power_protocol(name, True)       # an output blanked by the power protocol is not woken by being enabled
        return
    try:
        await dpms(False, name)
        _kept_off.add(name)
        _watch_uevents()
        return
    except RuntimeError as exc:
        logger.info('The power protocol did not blank {} ({}): disabling the output', name, exc)
    if known is not None and known.active:
        _last_geometry[name] = known
    result = await run(['wlr-randr', '--output', name, '--off'], timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'wlr-randr exited with {result.returncode}')


# Outputs WE blanked and that must stay so. A monitor without signal goes into deep standby and, when it comes back, the compositor
# reapplies its configuration and switches the output on again (Raspberry Pi + a monitor that drops its link: after ~30 s). The
# kernel tells (a `change` uevent of the DRM device) and the blanking is repeated: no polling, the listener exists only while
# something is kept off.
_kept_off: set[str] = set()
_uevents: dict = {}


def _watch_uevents():
    if _uevents.get('fd') is not None:
        return
    import socket
    try:
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15)       # NETLINK_KOBJECT_UEVENT
        sock.bind((0, 1))                                                    # the kernel's own group
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
    except (OSError, RuntimeError) as exc:
        logger.debug('No uevent listener, a blanked output is not kept off: {}', exc)
        return
    _uevents.update(fd=sock, loop=loop, timer=None)
    loop.add_reader(sock.fileno(), _uevent_readable)


def _uevent_readable():
    sock, loop = _uevents['fd'], _uevents['loop']
    relevant = False
    try:
        while True:
            data = sock.recv(8192)
            relevant = relevant or (b'SUBSYSTEM=drm' in data and b'ACTION=change' in data)
    except BlockingIOError:
        pass
    if relevant:
        if _uevents['timer']:
            _uevents['timer'].cancel()
        _uevents['timer'] = loop.call_later(0.3, lambda: loop.create_task(keep_off()))


async def keep_off():
    """Blank again every output we switched off that has been switched on by something else; stop listening when none is left."""
    if not _kept_off:
        _stop_uevents()
        return
    try:
        now = {o.name: o for o in await outputs()}
        for name in list(_kept_off):
            if name not in now:
                continue                              # not there (yet): done when it comes back
            if now[name].active:
                logger.info('Output {} was switched on by itself: blanking it again', name)
                await dpms(False, name)
    except Exception as exc:
        logger.warning('Cannot keep {} off: {}', sorted(_kept_off), exc)


def _stop_uevents():
    sock = _uevents.pop('fd', None)
    if sock is not None:
        _uevents['loop'].remove_reader(sock.fileno())
        sock.close()
    _uevents.clear()


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
    _kept_off.clear()
    _stop_uevents()
    await dpms(True)
