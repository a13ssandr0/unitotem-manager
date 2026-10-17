"""
The accounts of the web interface ARE the operating-system accounts.

A person logs in with a user name and password that PAM accepts (the same ones as on the machine), and may do so only
when the account is root or a member of the group `unitotem`. Creating a user in UniTotem creates the system account and
puts it in that group; what each user may do inside UniTotem (the capabilities) stays in users.json.

Reading accounts uses `pwd`/`grp`. Creating and changing them has no library in Python's standard set, so it runs the
shadow-utils tools (`useradd`, `userdel`, `groupadd`, `gpasswd`, `chpasswd`) with an argument list (never a shell) and the
secret on standard input (never on a command line). The manager runs as root.
"""
import grp
import pwd
import re
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

GROUP = 'unitotem'
GECOS = 'UniTotem user'           # marks the accounts UniTotem created: only those are ever deleted from the system
PAM_SERVICE = 'unitotem-manager'  # /etc/pam.d/unitotem-manager, shipped by the package; 'login' is the stand-in when it is missing
SHADOW = Path('/etc/shadow')
NAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')       # Debian's NAME_REGEX: what useradd accepts without --badname
MAX_PASSWORD = 512


class OSUserError(Exception):
    """A refusal the person can be told about."""


def valid_name(name: str) -> bool:
    return isinstance(name, str) and NAME.fullmatch(name) is not None


def _run(args: list[str], secret: Optional[str] = None):
    result = subprocess.run(args, input=secret, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.error('{} failed ({}): {}', args[0], result.returncode, result.stderr.strip())
        raise OSUserError(result.stderr.strip() or f'{args[0]} failed')


# ── who may log in ───────────────────────────────────────────────────────────

def _gid() -> Optional[int]:
    try:
        return grp.getgrnam(GROUP).gr_gid
    except KeyError:
        return None


def exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except (KeyError, TypeError):
        return False


def members() -> list[str]:
    """The accounts of the group (supplementary members and accounts whose primary group it is), root excluded."""
    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        return []
    names = set(group.gr_mem)
    names.update(entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == group.gr_gid)
    return sorted(name for name in names if name != 'root' and exists(name))


def may_login(name: str) -> bool:
    """root, or a member of the group `unitotem`."""
    try:
        entry = pwd.getpwnam(name)
    except (KeyError, TypeError):
        return False
    if entry.pw_uid == 0:
        return True
    gid = _gid()
    if gid is None:
        return False
    return entry.pw_gid == gid or name in grp.getgrgid(gid).gr_mem


def authenticate(name: str, password: str) -> bool:
    """PAM says the password is right (and the account is valid), and the account is allowed to use UniTotem."""
    if not isinstance(name, str) or not isinstance(password, str) or not name or len(password) > MAX_PASSWORD:
        return False
    if not may_login(name):
        return False                # no PAM exchange at all for an account that could not come in anyway
    import pam
    service = PAM_SERVICE if Path('/etc/pam.d', PAM_SERVICE).exists() else 'login'
    return bool(pam.pam().authenticate(name, password, service=service))


# ── managing them ────────────────────────────────────────────────────────────

def ensure_group():
    if _gid() is None:
        _run(['groupadd', '--system', GROUP])
        logger.info('Created the group {}', GROUP)


def create_user(name: str, password: str, hashed: bool = False):
    """A new system account in the group (no home, no shell: it exists to log in to the web interface)."""
    if not valid_name(name):
        raise OSUserError('The user name must be lower case letters, digits, "_" or "-", starting with a letter, '
                          'at most 32 characters')
    if exists(name):
        raise OSUserError('An account with this name already exists on the system')
    ensure_group()
    _run(['useradd', '--no-create-home', '--shell', '/usr/sbin/nologin', '--comment', GECOS, '--groups', GROUP, name])
    try:
        set_password(name, password, hashed)
    except OSUserError:
        _run(['userdel', name])         # no half-made account
        raise
    logger.info('Created the account {}', name)


def set_password(name: str, password: str, hashed: bool = False):
    if '\n' in password or '\0' in password or ':' in name:
        raise OSUserError('Invalid characters in the password')
    _run(['chpasswd', *(['--encrypted'] if hashed else [])], f'{name}:{password}\n')


def add_to_group(name: str):
    ensure_group()
    _run(['gpasswd', '--add', name, GROUP])


def remove_user(name: str):
    """Out of the group (it can no longer log in); the account itself is deleted only if UniTotem created it."""
    if name == 'root' or not exists(name):
        return
    entry = pwd.getpwnam(name)
    if entry.pw_gecos == GECOS and entry.pw_uid != 0:
        _run(['userdel', name])
        logger.info('Deleted the account {}', name)
    elif _gid() is not None and name in grp.getgrgid(_gid()).gr_mem:
        _run(['gpasswd', '--delete', name, GROUP])
        logger.info('Removed {} from the group {}', name, GROUP)


# ── backup: the password is the hash of /etc/shadow, never the password ──────

def password_hash(name: str) -> Optional[str]:
    try:
        for line in SHADOW.read_text().splitlines():
            fields = line.split(':')
            if fields[0] == name:
                return fields[1] or None
    except OSError as e:
        logger.error('Cannot read {}: {}', SHADOW, e)
    return None


def restore_user(name: str, shadow_hash: str):
    """The account with the password it had when the backup was made (created if it is missing)."""
    if not valid_name(name) or not shadow_hash or '\n' in shadow_hash:
        raise OSUserError(f'Invalid account in the backup: {name!r}')
    if exists(name):
        add_to_group(name)
        set_password(name, shadow_hash, hashed=True)
    else:
        create_user(name, shadow_hash, hashed=True)
