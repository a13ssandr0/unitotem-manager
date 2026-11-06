"""
The accounts of the web interface ARE the operating-system accounts.

A person logs in with a user name and password that PAM accepts (the same ones as on the machine), and may do so only
when the account is a member of the group `users`. An administrator of UniTotem is also a member of the group `sudo`.
root never logs in to the web interface. Creating a user in UniTotem creates the system account and puts it in the
group `users` (and `sudo` for an administrator); what each user may do inside UniTotem (the capabilities) stays in
users.json.

The first-access window (before the first administrator exists) is marked by root's membership of the group
`unitotem-first-admin`: the sshd marker that lets root in until setup closes it.

Reading accounts uses `pwd`/`grp`. Creating and changing them has no library in Python's standard set, so it runs the
shadow-utils tools (`adduser`, `deluser`, `userdel`, `gpasswd`, `usermod`, `chpasswd`) with an argument list (never a
shell) and the secret on standard input (never on a command line). The manager runs as root.
"""
import grp
import pwd
import re
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from utils import passwords

USERS_GROUP = 'users'                      # web access
ADMIN_GROUP = 'sudo'                       # administrators of UniTotem
FIRST_ACCESS_GROUP = 'unitotem-first-admin'  # root's marker while the first-access window is open
GECOS = 'UniTotem user'           # marks the accounts UniTotem created: only those are ever deleted from the system
PAM_SERVICE = 'unitotem-manager'  # /etc/pam.d/unitotem-manager, shipped by the package; 'login' is the stand-in when it is missing
NOLOGIN = '/usr/sbin/nologin'
SHELL = '/bin/bash'
SHADOW = Path('/etc/shadow')
NAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')       # Debian's NAME_REGEX: what adduser accepts
MAX_PASSWORD = 512


class OSUserError(Exception):
    """A refusal the person can be told about."""


def valid_name(name: str) -> bool:
    return isinstance(name, str) and NAME.fullmatch(name) is not None


CHPASSWD_ENV = {'LANG': 'C.UTF-8', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin'}   # English messages from PAM, whatever the locale
BAD_PASSWORD = 'BAD PASSWORD: '


def _run(args: list[str], secret: Optional[str] = None, env: Optional[dict] = None):
    result = subprocess.run(args, input=secret, capture_output=True, text=True, timeout=30, env=env)
    if result.returncode != 0:
        logger.error('{} failed ({}): {}', args[0], result.returncode, result.stderr.strip())
        raise OSUserError(result.stderr.strip() or f'{args[0]} failed')


# ── who may log in ───────────────────────────────────────────────────────────

def _group(name: str) -> Optional[grp.struct_group]:
    try:
        return grp.getgrnam(name)
    except KeyError:
        return None


def _is_member(name: str, group_name: str) -> bool:
    """Supplementary member or primary group of the account."""
    group = _group(group_name)
    if group is None:
        return False
    if name in group.gr_mem:
        return True
    try:
        return pwd.getpwnam(name).pw_gid == group.gr_gid
    except (KeyError, TypeError):
        return False


def exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except (KeyError, TypeError):
        return False


def members() -> list[str]:
    """The accounts that may log in to the web interface, root excluded."""
    group = _group(USERS_GROUP)
    if group is None:
        return []
    names = set(group.gr_mem)
    names.update(entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == group.gr_gid)
    return sorted(name for name in names if name != 'root' and exists(name))


def may_login(name: str) -> bool:
    """A member of the group `users`; root never."""
    try:
        entry = pwd.getpwnam(name)
    except (KeyError, TypeError):
        return False
    if entry.pw_uid == 0:
        return False
    return _is_member(name, USERS_GROUP)


def is_admin(name: str) -> bool:
    return _is_member(name, ADMIN_GROUP)


def admin_exists() -> bool:
    return any(may_login(name) and is_admin(name) for name in members())


def authenticate(name: str, password: str) -> bool:
    """PAM says the password is right (and the account is valid), and the account is allowed to use UniTotem."""
    if not isinstance(name, str) or not isinstance(password, str) or not name or not password \
            or len(password) > MAX_PASSWORD:
        return False
    if not may_login(name):
        return False                # no PAM exchange at all for an account that could not come in anyway
    import pam
    service = PAM_SERVICE if Path('/etc/pam.d', PAM_SERVICE).exists() else 'login'
    return bool(pam.pam().authenticate(name, password, service=service))


# ── managing them ────────────────────────────────────────────────────────────

def _delete_account(name: str, admin: bool):
    """Rollback of a half-made account: its home goes with it when adduser made one."""
    args = ['deluser', '--remove-home', '--', name] if admin else ['deluser', '--', name]
    try:
        _run(args)
    except OSUserError:
        logger.error('Could not remove the half-made account {}', name)


def create_user(name: str, password: str, hashed: bool = False, admin: bool = False):
    """A new system account in the group `users`; an administrator also gets a home and the group `sudo`."""
    if not valid_name(name):
        raise OSUserError('The user name must be lower case letters, digits, "_" or "-", starting with a letter, '
                          'at most 32 characters')
    if exists(name):
        raise OSUserError('An account with this name already exists on the system')
    if not hashed:
        # refused before any command: a weak password must not create the account and then delete it
        problems = passwords.validate_password(password, name)
        if problems:
            raise OSUserError('; '.join(problems))
    if admin:
        _run(['adduser', '--disabled-password', '--comment', GECOS, '--', name])
    else:
        _run(['adduser', '--disabled-password', '--no-create-home', '--shell', NOLOGIN, '--comment', GECOS,
              '--', name])
    try:
        if admin:
            _run(['gpasswd', '--add', name, ADMIN_GROUP])
        set_password(name, password, hashed)
        if not _is_member(name, USERS_GROUP):
            _run(['gpasswd', '--add', name, USERS_GROUP])    # adduser.conf may not add it: web access depends on it
    except Exception:
        _delete_account(name, admin)   # no half-made account
        raise
    logger.info('Created the account {}', name)


# A crypt(3) hash as /etc/shadow keeps it ($id$[params$]salt$hash): the characters of the format and nothing else, never a colon or a line break
_HASH = re.compile(r'\$[A-Za-z0-9./$,=+-]{3,300}')


def set_password(name: str, password: str, hashed: bool = False):
    # chpasswd reads LINES of `name:secret`: the line is built only from a valid name and a secret that cannot end it
    if not valid_name(name):
        raise OSUserError('Invalid user name')
    if hashed and not _HASH.fullmatch(password):
        raise OSUserError('Not a password hash')
    if not isinstance(password, str) or '\n' in password or '\r' in password or '\0' in password:
        raise OSUserError('Invalid characters in the password')
    try:
        _run(['chpasswd', *(['--encrypted'] if hashed else [])], f'{name}:{password}\n', env=CHPASSWD_ENV)
    except OSUserError as e:
        # the raw stderr is already in the log (_run); the person gets only the PAM reasons or a generic refusal
        reasons = [line[len(BAD_PASSWORD):] for line in str(e).splitlines() if line.startswith(BAD_PASSWORD)]
        if reasons:
            raise OSUserError('; '.join(reasons)) from None
        raise OSUserError('The password was refused') from None


def set_admin(name: str, admin: bool):
    """Grants or revokes the administration of UniTotem: the group `sudo` and a login shell."""
    if not valid_name(name) or not exists(name):
        raise OSUserError('No such account on the system')
    if name == 'root':
        raise OSUserError('The root account is not managed by UniTotem')
    if admin:
        if not _is_member(name, ADMIN_GROUP):
            _run(['gpasswd', '--add', name, ADMIN_GROUP])
        _run(['usermod', '--shell', SHELL, '--', name])
        if not Path(pwd.getpwnam(name).pw_dir).is_dir():
            _run(['mkhomedir_helper', name])
    else:
        if _is_member(name, ADMIN_GROUP):
            _run(['gpasswd', '--delete', name, ADMIN_GROUP])
        _run(['usermod', '--shell', NOLOGIN, '--', name])   # the home stays
    logger.info('{} is {} an administrator', name, 'now' if admin else 'no longer')


def remove_user(name: str):
    """Out of the group `users` (it can no longer log in); the account itself is deleted only if UniTotem created it."""
    if name == 'root' or not exists(name):
        return
    entry = pwd.getpwnam(name)
    if entry.pw_gecos == GECOS and entry.pw_uid != 0:
        _run(['userdel', '--', name])
        logger.info('Deleted the account {}', name)
    else:
        group = _group(USERS_GROUP)
        if group is not None and name in group.gr_mem:
            _run(['gpasswd', '--delete', name, USERS_GROUP])
            logger.info('Removed {} from the group {}', name, USERS_GROUP)
        elif entry.pw_gid == group.gr_gid if group is not None else False:
            # a primary group cannot be left with gpasswd: the account is not ours to re-home
            raise OSUserError(f'{name} has `{USERS_GROUP}` as its primary group: change it on the system first')


# ── first access ─────────────────────────────────────────────────────────────

def first_access_open() -> bool:
    group = _group(FIRST_ACCESS_GROUP)
    return group is not None and 'root' in group.gr_mem


def close_first_access():
    """Ends the first-access window: root leaves its marker group, gets no password and no login shell. Idempotent."""
    if first_access_open():
        _run(['gpasswd', '--delete', 'root', FIRST_ACCESS_GROUP])
        logger.info('Closed the first-access window')
    _run(['usermod', '-p', '*', 'root'])            # '*' locks the password without the '!' prefix
    _run(['usermod', '--shell', SHELL, 'root'])


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


def restore_user(name: str, shadow_hash: str, admin: bool = False):
    """The account with the password it had when the backup was made (created if it is missing)."""
    if not valid_name(name) or name == 'root' or not shadow_hash or '\n' in shadow_hash:
        raise OSUserError(f'Invalid account in the backup: {name!r}')
    if exists(name):
        if not _is_member(name, USERS_GROUP):
            _run(['gpasswd', '--add', name, USERS_GROUP])
        set_password(name, shadow_hash, hashed=True)
        set_admin(name, admin)
    else:
        create_user(name, shadow_hash, hashed=True, admin=admin)
