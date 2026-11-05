"""
CEC through v4l-utils' cec-ctl. Only a TV or a monitor on an HDMI port with CEC wired to a CEC adapter (the Pi's
own HDMI, or a USB adapter) answers; most nodes have no /dev/cec* at all, which just makes the mechanism
unavailable.

cec-ctl is driven through its documented commands (--playback, --standby, --image-view-on, --active-source, --give-device-power-status).
The adapter of an output is matched through the DRM connector the kernel reports for it (a Raspberry Pi 4 has two adapters). The
parsers read stdout, not the exit code of the transmit, because a transmit can report success with no reply.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ._exec import ToolMissing, ToolTimeout, run

OSD_NAME = 'UniTotem'   # what the TV shows in its source list (14 characters at most)

_locks: dict[str, asyncio.Lock] = {}


def _lock(device: str) -> asyncio.Lock:
    return _locks.setdefault(device, asyncio.Lock())


@dataclass(frozen=True)
class CecDevice:
    path: str            # /dev/cec0
    driver: str          # vc4_hdmi
    name: str            # vc4-hdmi-0


@dataclass(frozen=True)
class AdapterInfo:
    """What `cec-ctl -d DEV` reports about one adapter."""
    drm_card: Optional[int]          # 'DRM Connector Info : card 1, connector 35'
    drm_connector: Optional[int]
    physical_address: Optional[str]  # None while no sink is attached (f.f.f.f)
    configured: bool                 # a logical address is claimed (mask not 0x0000)


def parse_list_devices(text: str) -> list[CecDevice]:
    """`cec-ctl --list-devices`: a block per adapter, 'driver (adapter):' then an indented '/dev/cecN' line."""
    devices = []
    name = driver = None
    for raw in text.splitlines():
        if raw and not raw.startswith((' ', '\t')):
            m = re.match(r'^(.*?)(?: \((.*)\))?:\s*$', raw.strip())
            driver, name = (m.group(1), m.group(2) or '') if m else (raw.strip(), '')
            continue
        m = re.search(r'(/dev/cec\d+)', raw)
        if m and driver is not None:
            devices.append(CecDevice(m.group(1), driver, name or ''))
    return devices


def parse_adapter_info(text: str) -> AdapterInfo:
    card = connector = None
    m = re.search(r'DRM Connector Info\s*:\s*card (\d+), connector (\d+)', text)
    if m:
        card, connector = int(m.group(1)), int(m.group(2))
    m = re.search(r'^\s*Physical Address\s*:\s*([0-9a-f]\.[0-9a-f]\.[0-9a-f]\.[0-9a-f])\s*$', text, re.M)
    address = m.group(1) if m and m.group(1) != 'f.f.f.f' else None
    m = re.search(r'Logical Address Mask\s*:\s*0x([0-9a-f]+)', text)
    configured = bool(m and int(m.group(1), 16))
    return AdapterInfo(card, connector, address, configured)


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


async def adapter_info(device: str) -> AdapterInfo:
    async with _lock(device):
        result = await run(['cec-ctl', '-d', device], timeout=10)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'cec-ctl exited with {result.returncode}')
    return parse_adapter_info(result.stdout)


def connector_id(output: str, card: int, root: str = '/sys/class/drm') -> Optional[int]:
    """The DRM connector id of an output (`/sys/class/drm/card1-HDMI-A-1/connector_id`)."""
    try:
        return int((Path(root) / f'card{card}-{output}' / 'connector_id').read_text().strip())
    except (OSError, ValueError):
        return None


async def device_for_output(output: str, adapters: list[CecDevice]) -> Optional[str]:
    """The adapter wired to an output: the one whose DRM connector is the output's, else the only adapter that
    reports no connector (a USB adapter in the HDMI line); an adapter of another connector is never used."""
    unbound = []
    for adapter in adapters:
        try:
            info = await adapter_info(adapter.path)
        except (RuntimeError, ToolMissing, ToolTimeout):
            continue
        if info.drm_card is None:
            unbound.append(adapter.path)
        elif connector_id(output, info.drm_card) == info.drm_connector:
            return adapter.path
    return unbound[0] if len(unbound) == 1 else None


async def _transmit(device: str, *args: str):
    async with _lock(device):
        result = await run(['cec-ctl', '-d', device, *args], timeout=20)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'cec-ctl exited with {result.returncode}')
    return result


async def _ready(device: str) -> AdapterInfo:
    """Claim a playback logical address once (the kernel keeps it and claims it again after a hot-plug): without one
    there is no source to speak as, and the TV lists the node by its OSD name."""
    info = await adapter_info(device)
    if not info.configured:
        await _transmit(device, '--playback', '--osd-name', OSD_NAME)
        info = await adapter_info(device)
    return info


async def set_power(device: str, on: bool, address: str = '0'):
    """Switch the display at logical address `address` (0 is the TV) on or off."""
    info = await _ready(device)
    if on:
        await _transmit(device, '--to', address, '--image-view-on')
        if info.physical_address is None:
            raise RuntimeError('the TV gives this output no CEC physical address (not connected?)')
        await _transmit(device, '--active-source', f'phys-addr={info.physical_address}')
    else:
        await _transmit(device, '--to', address, '--standby')


async def power_state(device: str, address: str = '0') -> Optional[str]:
    try:
        await _ready(device)
        result = await _transmit(device, '--to', address, '--give-device-power-status')
    except (RuntimeError, ToolMissing, ToolTimeout):
        return None
    return parse_power_status(result.stdout)
