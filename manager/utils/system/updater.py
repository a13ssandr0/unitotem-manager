"""
Thin client for the device's image updater, ``/usr/sbin/unitotem-update`` (contract: AB-DESIGN.md in
unitotem-system). The manager only starts it and reads its answers, it never downloads or writes
images itself.
"""
import asyncio
import json
from typing import Any

from loguru import logger

UPDATER = '/usr/sbin/unitotem-update'

# Documented exit codes; every one has a meaning, none is a generic error.
EXIT_OK = 0
EXIT_NOTHING_TO_DO = 10
EXIT_FAILED = 2
EXIT_ABSENT = 127

TIMEOUT = 180  # a check may have to download and verify the manifest


def parse_output(returncode: int, stdout: str) -> dict[str, Any]:
    """Turn the updater's (exit code, stdout) pair into a dict the web UI can use."""
    if returncode == EXIT_ABSENT:
        return {'supported': False}
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
    except (ValueError, IndexError):
        return {'supported': True, 'error': 'Unreadable answer from the updater', 'error_code': 'bad-output'}
    if not isinstance(data, dict):
        return {'supported': True, 'error': 'Unreadable answer from the updater', 'error_code': 'bad-output'}
    data['supported'] = True
    data['returncode'] = returncode
    if returncode == EXIT_FAILED and 'error' not in data:
        data['error'], data['error_code'] = 'The updater failed', 'failed'
    return data


async def run(*args: str) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
                UPDATER, '--json', *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return parse_output(EXIT_ABSENT, '')
    try:
        out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {'supported': True, 'error': 'The updater did not answer in time', 'error_code': 'timeout'}
    if err:
        logger.debug('unitotem-update {}: {}', ' '.join(args), err.decode(errors='replace').strip())
    return parse_output(proc.returncode, out.decode(errors='replace'))


async def status() -> dict[str, Any]:
    return await run('status')


async def check() -> dict[str, Any]:
    return await run('check')
