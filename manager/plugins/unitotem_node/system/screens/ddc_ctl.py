"""
DDC/CI through ddcutil: the subprocess side of ddc.py (which only parses).

A monitor is addressed by its I2C bus (`--bus N`), one VCP code per call, with one lock per bus: two transactions on
the same bus at once is exactly how "Maximum retries exceeded" was reproduced. Everything is async and runs the
tool in a thread.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from ._exec import ToolMissing, ToolTimeout, run
from .ddc import (BusNotFound, CommunicationFailed, DdcError, DetectedDisplay, VcpValue,
                  parse_capabilities, parse_detect, parse_getvcp_terse, parse_version, supports_ddc)

VCP_INPUT = '60'
VCP_VOLUME = '62'
VCP_MUTE = '8D'
VCP_POWER = 'D6'

# MCCS: 0xD6 values. 01 = on, 04 = standby ("off" for most panels), 05 = hard off. The monitor's own
# capabilities decide which of them it accepts (see power_off_value).
POWER_ON = 0x01
POWER_OFF_PREFERENCE = (0x04, 0x05, 0x02, 0x03)
MUTE_ON, MUTE_OFF = 0x01, 0x02

_locks: dict[int, asyncio.Lock] = {}


def _lock(bus: int) -> asyncio.Lock:
    return _locks.setdefault(bus, asyncio.Lock())


async def available() -> tuple[bool, str]:
    """(usable, reason). Usable means ddcutil is installed and new enough to trust."""
    try:
        result = await run(['ddcutil', '--version'], timeout=10)
    except ToolMissing:
        return False, 'ddcutil is not installed'
    except ToolTimeout as exc:
        return False, str(exc)
    version = parse_version(result.stdout)
    if not supports_ddc(version):
        return False, f'ddcutil 2.0 or newer is required (found {version[0]}.{version[1]})' if version \
            else 'ddcutil gave no version'
    return True, ''


async def detect() -> list[DetectedDisplay]:
    result = await run(['ddcutil', 'detect', '--brief'], timeout=60)
    return parse_detect(result.stdout)


async def capabilities(bus: int) -> dict[str, dict[str, str]]:
    """What the monitor says it supports: ~30 s cold, so it is only called from the background probe."""
    async with _lock(bus):
        result = await run(['ddcutil', '--bus', str(bus), 'capabilities'], timeout=90)
    return parse_capabilities(result.stdout)


RETRIES = 3          # DDC/CI is flaky on many monitors: a transaction that fails once often works the next time
RETRY_DELAY = 0.6


async def getvcp(bus: int, code: str) -> Optional[VcpValue]:
    """The current value of one VCP feature, None if the monitor does not support it."""
    for attempt in range(RETRIES):
        async with _lock(bus):
            result = await run(['ddcutil', '--bus', str(bus), '--terse', 'getvcp', code], timeout=15)
        try:
            return parse_getvcp_terse(result.stdout)
        except CommunicationFailed:
            if attempt == RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_DELAY)


async def setvcp(bus: int, code: str, value: int):
    """Set one VCP feature. --noverify: many monitors cannot answer the read-back that follows a write, and
    the failure of that read-back would be reported as a failure of the write."""
    for attempt in range(RETRIES):
        async with _lock(bus):
            result = await run(['ddcutil', '--bus', str(bus), '--noverify', 'setvcp', code, str(value)], timeout=15)
        text = result.stdout + result.stderr
        if result.returncode == 0:
            return
        if 'does not exist' in text:
            raise BusNotFound(text.strip())
        if 'communication failed' in text.lower() or 'retries exceeded' in text.lower():
            if attempt < RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY)
                continue
            raise CommunicationFailed(text.strip())
        raise DdcError(text.strip() or f'ddcutil exited with {result.returncode}')


def power_off_value(caps: dict[str, dict[str, str]]) -> int:
    """The 0xD6 value to switch a monitor off with: the first of the usual ones it advertises. A monitor that
    advertised nothing is assumed to take standby (04), the one every panel documents."""
    advertised = {int(v, 16) for v in caps.get(VCP_POWER, {})}
    for value in POWER_OFF_PREFERENCE:
        if value in advertised:
            return value
    return 0x04


WAKE_RESET_DELAY = 1.0


async def set_power(bus: int, on: bool, caps: Optional[dict] = None, wake_reset: bool = False):
    """`wake_reset`: the monitor was put to standby with 0xD6 <- 04 by us. Some (measured: LG Ultrafine) ignore a plain 0xD6 <- 01 in
    that state and wake only after 0xD6 <- 05 and then 01. It is not sent to a monitor believed to be on: 05 would switch it off for
    a moment."""
    if on and wake_reset:
        logger.info('DDC: bus {} wake-up with reset (0xD6 <- 05, then 01)', bus)
        await setvcp(bus, VCP_POWER, 0x05)
        await asyncio.sleep(WAKE_RESET_DELAY)
        await setvcp(bus, VCP_POWER, POWER_ON)
        return
    value = POWER_ON if on else power_off_value(caps or {})
    logger.info('DDC: bus {} power {} (0xD6 <- {:02x})', bus, 'on' if on else 'off', value)
    await setvcp(bus, VCP_POWER, value)


async def power_state(bus: int) -> Optional[str]:
    """'on', 'standby' or 'off', None when the monitor does not say (many do not answer while off)."""
    try:
        value = await getvcp(bus, VCP_POWER)
    except DdcError:
        return None
    if value is None:
        return None
    return {POWER_ON: 'on', 0x04: 'standby', 0x05: 'off', 0x02: 'standby', 0x03: 'standby'}.get(value.value)


FEATURES = (VCP_INPUT, VCP_VOLUME, VCP_MUTE, VCP_POWER)


async def probe_features(bus: int, caps: dict) -> list[str]:
    """The VCP codes this monitor supports. The capabilities string is the best source, but many monitors never
    answer for it ("Maximum DDC retries exceeded"), so each code we use is asked for instead; a transient
    communication failure is retried once before the code is counted as unsupported."""
    if caps:
        return [code for code in FEATURES if code in caps]
    found = []
    for code in FEATURES:
        try:
            if await getvcp(bus, code) is not None:
                found.append(code)
        except DdcError:
            pass
    return found
