"""
Thin client for the device's image updater, ``/usr/sbin/unitotem-update`` (contract: AB-DESIGN.md in
unitotem-system). The manager only starts it and reads its answers, it never downloads or writes
images itself.
"""
import asyncio
import json
from typing import Any, Optional

from loguru import logger

from utils.notifications import notifications

UPDATER = '/usr/sbin/unitotem-update'

# Documented exit codes; every one has a meaning, none is a generic error.
EXIT_OK = 0
EXIT_NOTHING_TO_DO = 10
EXIT_BUSY = 12
EXIT_NO_ROLLBACK = 13
EXIT_FAILED = 2
EXIT_ABSENT = 127

TIMEOUT = 180  # a check may have to download and verify the manifest


SLOT_KEYS = ('slot', 'other_slot', 'default_slot')


def upper_slots(value):
    """Slot names are always shown in capitals (A, B), wherever they appear: the updater reports them as 'a'/'b'."""
    if isinstance(value, dict):
        return {k: (v.upper() if k in SLOT_KEYS and isinstance(v, str) and len(v) == 1 else upper_slots(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [upper_slots(v) for v in value]
    return value


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
    data = upper_slots(data)
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


# The slot a forced copy was started from (the running one), while that copy runs: the updater reports it as a plain `repair`.
copy_from: Optional[str] = None


async def status() -> dict[str, Any]:
    global copy_from
    result = await run('status')
    if not result.get('op'):
        copy_from = None
    elif copy_from:
        result['copy_from'] = copy_from
    return result


async def copy_to_other() -> dict[str, Any]:
    """Replaces the other slot with a copy of the running one; detached like fetch, progress is read from status()."""
    global copy_from
    running = (await run('status')).get('slot')
    result = await run('repair', '--force')
    if result.get('started') and running:
        copy_from = running
    return result


async def check() -> dict[str, Any]:
    return await run('check')


async def fetch(force: bool = False) -> dict[str, Any]:
    """Starts the download detached and returns at once; progress is read from status()."""
    return await run('fetch', *(['--force'] if force else []))


async def apply(reboot: bool = True) -> dict[str, Any]:
    return await run('apply', *(['--reboot'] if reboot else []))


async def confirm() -> dict[str, Any]:
    return await run('confirm')


async def rollback(reboot: bool = True) -> dict[str, Any]:
    return await run('rollback', *(['--reboot'] if reboot else []))


POLICY_MODES = ('off', 'download', 'stage', 'reboot', 'scheduled')


async def set_policy(mode: str, time: str) -> dict[str, Any]:
    """Sets the automatic update policy: what to do with a newer release, and the time of day for the scheduled restart."""
    return await run('policy', '--set-mode', mode, '--time', time)


async def reboot() -> dict[str, Any]:
    """Restarts the device; when an update is armed this is the restart that starts it (the only one that does on a Raspberry)."""
    return await run('reboot')


async def cancel() -> dict[str, Any]:
    return await run('cancel')


# What the last successful check found. The daily background check and the manual one both feed it, and it is
# what the navbar hint shows: a check that fails (no network) leaves the previous answer in place.
availability: dict[str, Any] = {'available': False, 'version': None, 'checked_at': None}


def record(result: dict[str, Any]) -> bool:
    """Remember a check result. Returns True when what the hint shows has changed."""
    import time
    if not result.get('supported') or result.get('error'):
        return False
    new = {'available': bool(result.get('available')), 'version': result.get('version'),
           'checked_at': int(time.time())}
    changed = (new['available'], new['version']) != (availability['available'], availability['version'])
    availability.update(new)
    # Every successful check, not only a change: the notification outlives a restart, and the first check after the
    # update was installed is what removes it
    if new['available']:
        notifications.notify('update.available', 'info', 'Update available',
                             f"Version {new['version'] or '(unknown)'} can be installed.", source='updates',
                             audience='admin', link='/settings/updates')
    else:
        notifications.resolve('update.available')
    return changed
