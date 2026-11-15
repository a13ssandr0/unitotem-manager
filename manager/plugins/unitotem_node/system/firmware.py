"""
Thin client for the firmware card, ``/usr/sbin/unitotem-firmware`` (contract: docs/system/firmware.md (unitotem repository)).
The manager only starts it and reads its answers, it never downloads or writes firmware files itself.
"""
import asyncio
import json
from typing import Any

from loguru import logger

FIRMWARE = '/usr/sbin/unitotem-firmware'

# Documented exit codes; every one has a meaning, none is a generic error.
EXIT_OK = 0
EXIT_NOTHING_TO_DO = 10
EXIT_BUSY = 12
EXIT_BLOCKED = 14
EXIT_FAILED = 2
EXIT_ABSENT = 127

TIMEOUT = 150  # a check may have to download and verify the files (at most 120 s in the helper)

# What a failure without a reason of its own is called, per exit code.
DEFAULT_ERRORS = {
    EXIT_FAILED: ('The firmware update failed', 'failed'),
    EXIT_BUSY: ('A firmware operation is already running', 'busy'),
    EXIT_BLOCKED: ('The firmware update is blocked', 'blocked'),
}


def parse_output(returncode: int, stdout: str) -> dict[str, Any]:
    """Turn the helper's (exit code, stdout) pair into a dict the web UI can use."""
    if returncode == EXIT_ABSENT:
        return {'supported': False}
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except (ValueError, IndexError):
        return {'supported': True, 'error': 'Unreadable answer from the firmware helper', 'error_code': 'bad-output'}
    if not isinstance(data, dict):
        return {'supported': True, 'error': 'Unreadable answer from the firmware helper', 'error_code': 'bad-output'}
    # the helper says itself when the device has no firmware card (`"supported": false`)
    data.setdefault('supported', True)
    data['returncode'] = returncode
    if returncode in DEFAULT_ERRORS and 'error' not in data:
        data['error'], data['error_code'] = DEFAULT_ERRORS[returncode]
    return data


async def run(*args: str) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
                FIRMWARE, '--json', *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return parse_output(EXIT_ABSENT, '')
    try:
        out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {'supported': True, 'error': 'The firmware helper did not answer in time', 'error_code': 'timeout'}
    if err:
        logger.debug('unitotem-firmware {}: {}', ' '.join(args), err.decode(errors='replace').strip())
    return parse_output(proc.returncode, out.decode(errors='replace'))


async def status() -> dict[str, Any]:
    return await run('status')


async def check() -> dict[str, Any]:
    return await run('check')


async def apply(files: bool = True, eeprom: bool = True) -> dict[str, Any]:
    """Starts the update detached and returns at once; progress is read from status(). Only the parts asked for are applied."""
    if not (isinstance(files, bool) and isinstance(eeprom, bool)):
        return {'supported': True, 'error': 'files and eeprom are true or false', 'error_code': 'bad-argument'}
    if not (files or eeprom):
        return {'supported': True, 'error': 'Nothing selected to apply', 'error_code': 'nothing-selected'}
    flags = (['--files'] if files else []) + (['--eeprom'] if eeprom else [])
    return await run('apply', *flags)
