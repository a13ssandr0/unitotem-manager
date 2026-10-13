"""
The operating-system account behind the web terminal.

The terminal runs `login` (see terminal.py), which asks for an account of the machine, not of the web UI. The first-access
setup can set that account's password so that a freshly installed node does not need a second manual step; this module
finds the account and sets the password.

The password is handed to `chpasswd` on its standard input: never on a command line, where every user of the machine
could read it in the process list. `chpasswd` is the standard, PAM-aware tool for this (it is not a systemd command).
"""
import asyncio
import pwd
from typing import Optional

from loguru import logger

_NO_LOGIN_SHELLS = ('nologin', 'false', 'sync', 'halt', 'shutdown')
PREFERRED = 'unitotem'


def _can_log_in(entry: pwd.struct_passwd) -> bool:
    shell = entry.pw_shell.rsplit('/', 1)[-1]
    return bool(entry.pw_shell) and shell not in _NO_LOGIN_SHELLS


def account() -> Optional[str]:
    """The account to give a password to: `unitotem` if the image has one, else the first regular user that can log
    in, else root (the only account of the kiosk image), else None."""
    entries = {e.pw_name: e for e in pwd.getpwall() if _can_log_in(e)}
    if PREFERRED in entries:
        return PREFERRED
    regular = sorted((e for e in entries.values() if 1000 <= e.pw_uid < 60000), key=lambda e: e.pw_uid)
    if regular:
        return regular[0].pw_name
    return 'root' if 'root' in entries else None


async def set_password(password: str) -> bool:
    """Sets the password of `account()`. False (and logged) if there is no such account or `chpasswd` fails."""
    name = account()
    if name is None or '\n' in password or '\0' in password:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            'chpasswd', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(f'{name}:{password}\n'.encode()), 30)
    except (OSError, asyncio.TimeoutError) as exc:
        logger.error('Cannot run chpasswd: {}', exc)
        return False
    if proc.returncode != 0:
        logger.error('chpasswd failed for {} (status {}): {}', name, proc.returncode, err.decode(errors='replace').strip())
        return False
    logger.success('Password of the operating-system account {} set from the first-access setup', name)
    return True
