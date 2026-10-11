"""
CEC through v4l-utils' cec-ctl. Only a TV or a monitor on an HDMI port with CEC wired to a CEC adapter (the Pi's
own HDMI, or a USB adapter) answers; most nodes have no /dev/cec* at all, which just makes the mechanism
unavailable.

cec-ctl is driven through its documented commands (--standby, --image-view-on, --active-source, --give-device-power-status). The
parsers read stdout, not the exit code of the transmit, because a transmit can report success with no reply.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from ._exec import ToolMissing, run

_locks: dict[str, asyncio.Lock] = {}


def _lock(device: str) -> asyncio.Lock:
    return _locks.setdefault(device, asyncio.Lock())


@dataclass(frozen=True)
class CecDevice:
    path: str            # /dev/cec0
    driver: str
    name: str


def parse_list_devices(text: str) -> list[CecDevice]:
    """`cec-ctl --list-devices`: a block per adapter, 'name (driver):' then an indented '/dev/cecN' line."""
    devices = []
    name = driver = None
    for raw in text.splitlines():
        if raw and not raw.startswith((' ', '\t')):
            m = re.match(r'^(.*?)(?: \((.*)\))?:\s*$', raw.strip())
            name, driver = (m.group(1), m.group(2) or '') if m else (raw.strip(), '')
            continue
        m = re.search(r'(/dev/cec\d+)', raw)
        if m and name is not None:
            devices.append(CecDevice(m.group(1), driver or '', name))
    return devices


def parse_power_status(text: str) -> Optional[str]:
    """'on', 'standby' or None, from the reply to --give-device-power-status ('pwr-state: on (0x00)')."""
    m = re.search(r'pwr-state:\s*(on|standby|to-on|to-standby)', text)
    if not m:
        return None
    return {'on': 'on', 'to-on': 'on', 'standby': 'standby', 'to-standby': 'standby'}[m.group(1)]


async def devices() -> list[CecDevice]:
    try:
        result = await run(['cec-ctl', '--list-devices'], timeout=10)
    except ToolMissing:
        return []
    return parse_list_devices(result.stdout)


async def _transmit(device: str, *args: str):
    async with _lock(device):
        result = await run(['cec-ctl', '-d', device, *args], timeout=20)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'cec-ctl exited with {result.returncode}')
    return result


async def set_power(device: str, on: bool, address: str = '0'):
    """Switch the display at logical address `address` (0 is the TV) on or off."""
    await _transmit(device, '--playback')   # claim a logical address first, or there is no source to speak as
    if on:
        await _transmit(device, '--to', address, '--image-view-on')
        await _transmit(device, '--active-source', 'phys-addr=1.0.0.0')
    else:
        await _transmit(device, '--to', address, '--standby')


async def power_state(device: str, address: str = '0') -> Optional[str]:
    try:
        result = await _transmit(device, '--to', address, '--give-device-power-status')
    except RuntimeError:
        return None
    return parse_power_status(result.stdout)
