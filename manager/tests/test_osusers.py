"""The web interface's accounts are the system's: group `users` for access, group `sudo` for administration, PAM for
the password, the first-access marker, and the backup of the password hash.

Nothing here runs a real account tool: pwd, grp and the shadow-utils commands are replaced by a fake that keeps the
accounts and groups in memory and records every argument list."""
import grp
import pwd
from pathlib import Path

import pytest

import pam
from utils import osusers
from utils.models import user as user_module
from utils.models.user import UserManager, UserPerms

USERS_GID = 990
SUDO_GID = 27
FIRST_ACCESS_GID = 991
OTHER_GID = 1000
NEW_GROUP_GID = 1100


class FakeSystem:
    """Accounts (struct_passwd), groups (struct_group), the password fields, and every command run."""

    def __init__(self, home: Path):
        self.home = home
        self.accounts: dict[str, pwd.struct_passwd] = {}
        self.groups: dict[str, grp.struct_group] = {}
        self.calls: list[tuple[list, object]] = []      # (argument list, stdin)
        self.passwords: dict[str, str] = {}             # the password field of /etc/shadow
        self.fail: set[str] = set()                     # programs that fail
        self.adduser_joins_users = True                 # Debian's USERGROUPS/USERS_GROUP: the primary group is users
        self._next_uid = 2000

    # ── lookups as pwd/grp give them ──
    def getpwnam(self, name):
        try:
            return self.accounts[name]
        except KeyError:
            raise KeyError(name) from None

    def getpwall(self):
        return list(self.accounts.values())

    def getgrnam(self, name):
        try:
            return self.groups[name]
        except KeyError:
            raise KeyError(name) from None

    # ── fixtures helpers ──
    def put_account(self, name, uid, gid, gecos='', shell='/bin/bash', home=None, make_home=True):
        home = Path(home) if home else self.home / name
        if make_home:
            home.mkdir(parents=True, exist_ok=True)
        self.accounts[name] = pwd.struct_passwd((name, 'x', uid, gid, gecos, str(home), shell))

    def put_group(self, name, gid, members=()):
        self.groups[name] = grp.struct_group((name, 'x', gid, list(members)))

    def members(self, group):
        return list(self.groups[group].gr_mem)

    def set_shell(self, name, shell):
        entry = self.accounts[name]
        self.accounts[name] = pwd.struct_passwd((entry.pw_name, entry.pw_passwd, entry.pw_uid, entry.pw_gid,
                                                 entry.pw_gecos, entry.pw_dir, shell))

    # ── the commands ──
    def run(self, args, secret=None):
        args = list(args)
        self.calls.append((args, secret))
        program = args[0]
        if program in self.fail:
            raise osusers.OSUserError(f'{program} failed')
        getattr(self, '_' + program)(args, secret)

    def _adduser(self, args, secret):
        name = args[-1]
        gecos = args[args.index('--comment') + 1]
        shell = args[args.index('--shell') + 1] if '--shell' in args else '/bin/bash'
        home = self.home / name
        if '--no-create-home' not in args:
            home.mkdir(parents=True, exist_ok=True)
        gid = USERS_GID if self.adduser_joins_users else NEW_GROUP_GID
        self.put_account(name, self._next_uid, gid, gecos, shell, home, make_home=False)
        self._next_uid += 1

    def _deluser(self, args, secret):
        self._drop(args[-1])

    _userdel = _deluser

    def _drop(self, name):
        self.accounts.pop(name, None)
        for group in self.groups.values():
            if name in group.gr_mem:
                group.gr_mem.remove(name)

    def _gpasswd(self, args, secret):
        action, name, group = args[1], args[2], args[3]
        members = self.groups[group].gr_mem
        if action == '--add':
            if name not in members:
                members.append(name)
        elif action == '--delete':
            if name in members:
                members.remove(name)
        else:
            raise AssertionError(f'unexpected gpasswd action {action}')

    def _usermod(self, args, secret):
        name = args[-1]
        if '--shell' in args:
            self.set_shell(name, args[args.index('--shell') + 1])
        if '-p' in args:
            self.passwords[name] = args[args.index('-p') + 1]

    def _chpasswd(self, args, secret):
        name, password = secret.rstrip('\n').split(':', 1)
        self.passwords[name] = password

    def _mkhomedir_helper(self, args, secret):
        Path(self.accounts[args[1]].pw_dir).mkdir(parents=True, exist_ok=True)

    # ── queries on the recorded calls ──
    def programs(self):
        return [args[0] for args, _ in self.calls]


@pytest.fixture
def system(monkeypatch, tmp_path):
    """root (in group users too: uid 0 still never logs in), alice (UniTotem-made administrator, supplementary member of
    users and sudo), bob (not a member), carol (primary group users), frank (supplementary member, no home yet), erin
    (administrator by primary group sudo, not in users: a nologin admin)."""
    fake = FakeSystem(tmp_path / 'home')
    fake.put_account('root', 0, 0, 'root', home='/root')
    fake.put_account('alice', 1001, OTHER_GID, osusers.GECOS)
    fake.put_account('bob', 1002, OTHER_GID, shell='/usr/sbin/nologin')
    fake.put_account('carol', 1003, USERS_GID, shell='/usr/sbin/nologin')
    fake.put_account('frank', 1004, OTHER_GID, shell='/usr/sbin/nologin', make_home=False)
    fake.put_account('erin', 1005, SUDO_GID, shell='/usr/sbin/nologin')
    fake.put_group('users', USERS_GID, ['root', 'alice', 'frank'])
    fake.put_group('sudo', SUDO_GID, ['alice'])
    fake.put_group('unitotem-first-admin', FIRST_ACCESS_GID, [])
    fake.put_group('staff', OTHER_GID, [])
    monkeypatch.setattr(osusers.pwd, 'getpwnam', fake.getpwnam)
    monkeypatch.setattr(osusers.pwd, 'getpwall', fake.getpwall)
    monkeypatch.setattr(osusers.grp, 'getgrnam', fake.getgrnam)
    monkeypatch.setattr(osusers, '_run', fake.run)
    return fake


@pytest.fixture
def pam_calls(monkeypatch):
    calls = []

    class FakePam:
        def authenticate(self, name, password, service=None):
            calls.append((name, password, service))
            return password == 'right'

    monkeypatch.setattr(pam, 'pam', FakePam)
    return calls


PASSWORD = 'pw-1234567890'


# ── who may log in ──────────────────────────────────────────────────────────

def test_root_never_logs_in_even_as_a_member(system):
    assert 'root' in system.members('users')
    assert osusers.may_login('root') is False


def test_members_of_users_may_log_in_as_supplementary_or_primary_group(system):
    assert osusers.may_login('alice')          # supplementary member
    assert osusers.may_login('frank')          # supplementary member, no home yet
    assert osusers.may_login('carol')          # primary group


def test_non_members_and_unknown_accounts_may_not_log_in(system):
    assert osusers.may_login('bob') is False
    assert osusers.may_login('erin') is False  # an administrator outside the group users
    assert osusers.may_login('nobody-here') is False
    assert osusers.may_login(None) is False


def test_members_list_is_the_web_accounts_without_root(system):
    assert osusers.members() == ['alice', 'carol', 'frank']


def test_members_is_empty_without_group_users(system):
    del system.groups['users']
    assert osusers.members() == []
    assert osusers.may_login('alice') is False


def test_is_admin_is_membership_of_sudo(system):
    assert osusers.is_admin('alice') is True   # supplementary
    assert osusers.is_admin('erin') is True    # primary group
    assert osusers.is_admin('carol') is False
    assert osusers.is_admin('bob') is False


def test_admin_exists_with_an_administrator_who_may_log_in(system):
    assert osusers.admin_exists() is True


def test_admin_exists_is_false_without_one(system):
    system.groups['sudo'].gr_mem.remove('alice')
    assert osusers.admin_exists() is False


def test_root_in_sudo_does_not_count_as_an_administrator(system):
    system.groups['sudo'].gr_mem.remove('alice')
    system.groups['sudo'].gr_mem.append('root')
    assert osusers.admin_exists() is False


def test_nologin_admin_outside_users_does_not_count(system):
    system.groups['sudo'].gr_mem.remove('alice')
    assert osusers.is_admin('erin') is True
    assert osusers.may_login('erin') is False
    assert osusers.admin_exists() is False     # erin is in sudo (primary) but not in users
    system.groups['users'].gr_mem.append('erin')
    assert osusers.admin_exists() is True


# ── authentication ──────────────────────────────────────────────────────────

def test_authenticate_asks_pam_for_a_member(system, pam_calls):
    assert osusers.authenticate('alice', 'right') is True
    assert osusers.authenticate('alice', 'wrong') is False
    assert [(name, password) for name, password, _ in pam_calls] == [('alice', 'right'), ('alice', 'wrong')]
    assert all(service in ('unitotem-manager', 'login') for _, _, service in pam_calls)


@pytest.mark.parametrize('name, password', [
    ('alice', ''),                 # empty password
    ('alice', 'x' * 513),          # longer than MAX_PASSWORD
    ('alice', None),
    (None, 'right'),
    ('root', 'right'),             # root never logs in
    ('bob', 'right'),              # not a member
    ('nobody-here', 'right'),      # no account
])
def test_authenticate_refuses_before_pam(system, pam_calls, name, password):
    assert osusers.authenticate(name, password) is False
    assert pam_calls == []


# ── creating accounts ───────────────────────────────────────────────────────

def test_admin_account_is_made_with_a_home_then_joins_sudo_then_gets_its_password(system):
    osusers.create_user('dave', PASSWORD, admin=True)
    assert system.calls[0] == (['adduser', '--disabled-password', '--comment', osusers.GECOS, '--', 'dave'], None)
    assert system.calls[1] == (['gpasswd', '--add', 'dave', 'sudo'], None)
    assert system.calls[2] == (['chpasswd'], f'dave:{PASSWORD}\n')
    assert system.passwords['dave'] == PASSWORD
    assert osusers.is_admin('dave') and osusers.may_login('dave')
    assert all(PASSWORD not in ' '.join(args) for args, _ in system.calls)


def test_user_account_is_made_without_home_and_with_nologin(system):
    osusers.create_user('eve', PASSWORD)
    assert system.calls[0] == (['adduser', '--disabled-password', '--no-create-home', '--shell', osusers.NOLOGIN,
                                '--comment', osusers.GECOS, '--', 'eve'], None)
    assert system.programs() == ['adduser', 'chpasswd']       # not in sudo
    assert osusers.may_login('eve') and not osusers.is_admin('eve')


def test_hashed_password_goes_to_chpasswd_encrypted(system):
    osusers.create_user('eve', '$y$j9T$salt$hash', hashed=True)
    assert system.calls[-1] == (['chpasswd', '--encrypted'], 'eve:$y$j9T$salt$hash\n')


def test_half_made_admin_is_deleted_with_its_home_when_chpasswd_fails(system):
    system.fail.add('chpasswd')
    with pytest.raises(osusers.OSUserError):
        osusers.create_user('dave', PASSWORD, admin=True)
    assert system.calls[-1][0] == ['deluser', '--remove-home', '--', 'dave']
    assert not osusers.exists('dave')


def test_half_made_user_is_deleted_without_home_option_when_chpasswd_fails(system):
    system.fail.add('chpasswd')
    with pytest.raises(osusers.OSUserError):
        osusers.create_user('eve', PASSWORD)
    assert system.calls[-1][0] == ['deluser', '--', 'eve']
    assert not osusers.exists('eve')


def test_account_not_in_users_after_adduser_is_added_explicitly(system):
    system.adduser_joins_users = False
    osusers.create_user('dave', PASSWORD, admin=True)
    assert system.calls[-1][0] == ['gpasswd', '--add', 'dave', 'users']
    assert osusers.may_login('dave') and osusers.is_admin('dave')


def test_existing_system_account_is_never_taken_over(system):
    with pytest.raises(osusers.OSUserError):
        osusers.create_user('bob', PASSWORD)
    assert system.calls == []


@pytest.mark.parametrize('name', ['', 'Dave', '1dave', 'da ve', 'dave:x', 'a' * 33, 'dave\n'])
def test_bad_names_are_refused_before_any_command(system, name):
    with pytest.raises(osusers.OSUserError):
        osusers.create_user(name, PASSWORD)
    assert system.calls == []


@pytest.mark.parametrize('password', ['x\nroot:y', 'x\ryy', 'x\0y'])
def test_password_cannot_smuggle_another_chpasswd_line(system, password):
    with pytest.raises(osusers.OSUserError):
        osusers.set_password('alice', password)
    assert system.calls == []


# ── administrators ──────────────────────────────────────────────────────────

def test_grant_admin_adds_sudo_sets_bash_and_creates_a_missing_home(system):
    osusers.set_admin('frank', True)
    assert system.calls == [
        (['gpasswd', '--add', 'frank', 'sudo'], None),
        (['usermod', '--shell', '/bin/bash', '--', 'frank'], None),
        (['mkhomedir_helper', 'frank'], None),
    ]
    assert osusers.is_admin('frank')
    assert (system.home / 'frank').is_dir()


def test_grant_admin_keeps_an_existing_home(system):
    osusers.set_admin('carol', True)
    assert 'mkhomedir_helper' not in system.programs()
    assert system.calls[0] == (['gpasswd', '--add', 'carol', 'sudo'], None)


def test_revoke_admin_removes_sudo_and_sets_nologin_and_keeps_the_home(system):
    osusers.set_admin('alice', False)
    assert system.calls == [
        (['gpasswd', '--delete', 'alice', 'sudo'], None),
        (['usermod', '--shell', osusers.NOLOGIN, '--', 'alice'], None),
    ]
    assert not osusers.is_admin('alice')
    assert osusers.may_login('alice')          # still a member of users
    assert not any(program in ('userdel', 'deluser') for program in system.programs())


def test_grant_admin_to_an_admin_skips_gpasswd(system):
    osusers.set_admin('alice', True)
    assert system.calls == [(['usermod', '--shell', '/bin/bash', '--', 'alice'], None)]


def test_revoke_admin_from_a_non_admin_skips_gpasswd(system):
    osusers.set_admin('bob', False)
    assert system.calls == [(['usermod', '--shell', osusers.NOLOGIN, '--', 'bob'], None)]


@pytest.mark.parametrize('name', ['root', 'nobody-here', 'Bad Name'])
def test_set_admin_refuses_root_and_unknown_accounts(system, name):
    with pytest.raises(osusers.OSUserError):
        osusers.set_admin(name, True)
    assert system.calls == []


# ── removing ────────────────────────────────────────────────────────────────

def test_a_unitotem_account_is_deleted(system):
    osusers.remove_user('alice')
    assert system.calls == [(['userdel', '--', 'alice'], None)]
    assert not osusers.exists('alice')


def test_a_foreign_supplementary_member_only_leaves_the_group(system):
    osusers.remove_user('frank')
    assert system.calls == [(['gpasswd', '--delete', 'frank', 'users'], None)]
    assert osusers.exists('frank')


def test_root_and_unknown_accounts_are_left_alone(system):
    osusers.remove_user('root')
    osusers.remove_user('nobody-here')
    assert system.calls == []


def test_a_foreign_primary_member_cannot_be_removed_and_nothing_is_run(system):
    with pytest.raises(osusers.OSUserError):
        osusers.remove_user('carol')
    assert system.calls == []


# ── restoring a backup ──────────────────────────────────────────────────────

def test_restore_creates_the_account_with_the_encrypted_hash(system):
    osusers.restore_user('erin2', '$y$j9T$salt$hash')
    assert system.calls[-1] == (['chpasswd', '--encrypted'], 'erin2:$y$j9T$salt$hash\n')
    assert osusers.may_login('erin2') and not osusers.is_admin('erin2')


def test_restore_admin_creates_it_in_sudo(system):
    osusers.restore_user('erin2', '$y$j9T$salt$hash', admin=True)
    assert osusers.is_admin('erin2')


def test_restore_of_an_existing_account_rejoins_users_and_gets_its_password_back(system):
    osusers.restore_user('bob', '$y$j9T$salt$hash', admin=True)
    assert system.calls[0] == (['gpasswd', '--add', 'bob', 'users'], None)
    assert (['chpasswd', '--encrypted'], 'bob:$y$j9T$salt$hash\n') in system.calls
    assert osusers.is_admin('bob')


@pytest.mark.parametrize('name, shadow_hash', [('root', '$y$a$b'), ('bad name', '$y$a$b'), ('dave', ''),
                                               ('dave', '$y$a$b\nroot:x')])
def test_restore_refuses_bad_backup_entries(system, name, shadow_hash):
    with pytest.raises(osusers.OSUserError):
        osusers.restore_user(name, shadow_hash)
    assert system.calls == []


def test_password_hash_is_read_from_shadow(tmp_path, monkeypatch):
    shadow = tmp_path / 'shadow'
    shadow.write_text('root:!:1:::::\nalice:$y$abc:1:::::\nlocked::1:::::\n')
    monkeypatch.setattr(osusers, 'SHADOW', shadow)
    assert osusers.password_hash('alice') == '$y$abc'
    assert osusers.password_hash('locked') is None
    assert osusers.password_hash('nobody') is None


# ── first access ────────────────────────────────────────────────────────────

def test_first_access_is_open_while_root_is_in_the_marker_group(system):
    assert osusers.first_access_open() is False
    system.groups['unitotem-first-admin'].gr_mem.append('root')
    assert osusers.first_access_open() is True


def test_first_access_is_closed_without_the_marker_group(system):
    del system.groups['unitotem-first-admin']
    assert osusers.first_access_open() is False


def test_close_first_access_runs_in_order_and_locks_with_star(system):
    system.groups['unitotem-first-admin'].gr_mem.append('root')
    osusers.close_first_access()
    assert system.calls == [
        (['gpasswd', '--delete', 'root', 'unitotem-first-admin'], None),
        (['usermod', '-p', '*', 'root'], None),
        (['usermod', '--shell', '/bin/bash', 'root'], None),
    ]
    assert osusers.first_access_open() is False
    assert system.passwords['root'] == '*'


def test_close_first_access_is_idempotent(system):
    system.groups['unitotem-first-admin'].gr_mem.append('root')
    osusers.close_first_access()
    system.calls.clear()
    osusers.close_first_access()               # root is not a member any more
    assert system.calls == [
        (['usermod', '-p', '*', 'root'], None),
        (['usermod', '--shell', '/bin/bash', 'root'], None),
    ]


def test_close_first_access_never_uses_bang(system):
    system.groups['unitotem-first-admin'].gr_mem.append('root')
    osusers.close_first_access()
    osusers.close_first_access()
    for args, _ in system.calls:
        assert '!' not in ' '.join(args)


# ── UserManager on the new model ────────────────────────────────────────────

@pytest.fixture
def users_file(monkeypatch, tmp_path):
    path = tmp_path / 'users.json'
    # cmdargs is a frozen pydantic settings object: its fields live in the instance __dict__, restored by monkeypatch
    monkeypatch.setitem(user_module.cmdargs.__dict__, 'users_file', path)
    return path


def test_user_manager_lists_web_accounts_and_derives_admin_from_sudo(system, users_file):
    manager = UserManager({'alice': {'permissions': [UserPerms.scheduler]}, 'ghost': {'permissions': [UserPerms.power]}})
    assert manager.users() == ['alice', 'carol', 'frank']       # 'ghost' has no system account, bob is no member
    assert manager.has_admin() is True
    assert manager.admins() == ['alice']
    assert manager.get_user('bob') is None                       # not in the group: no session
    assert manager.get_user('root') is None                      # root never logs in
    assert manager.get_user('carol').perms == set()              # in the group, no capability given yet
    assert manager.get_user('alice').perms == {UserPerms.scheduler, UserPerms.admin}


def test_user_manager_has_no_admin_when_sudo_is_empty(system, users_file):
    system.groups['sudo'].gr_mem.clear()
    manager = UserManager({'alice': {'permissions': [UserPerms.admin]}})
    assert manager.has_admin() is False
    assert manager.get_user('alice').perms == set()             # the stored record never grants admin


def test_user_manager_add_admin_creates_in_sudo_and_stores_other_capabilities(system, users_file):
    manager = UserManager({})
    manager.add_user('dave', PASSWORD, {UserPerms.admin, UserPerms.audio})
    assert osusers.is_admin('dave')
    assert manager.root['dave'].permissions == {UserPerms.audio}  # admin is not stored
    assert manager.get_user('dave').perms == {UserPerms.audio, UserPerms.admin}
    assert '"admin"' not in users_file.read_text()


def test_user_manager_add_refused_stores_nothing(system, users_file):
    system.fail.add('chpasswd')
    manager = UserManager({})
    with pytest.raises(osusers.OSUserError):
        manager.add_user('dave', PASSWORD, {UserPerms.audio})
    assert 'dave' not in manager.root
    assert not osusers.exists('dave')


def test_change_perms_grants_admin_through_sudo_and_stores_the_rest(system, users_file):
    manager = UserManager({})
    manager.change_perms('frank', {UserPerms.admin, UserPerms.power})
    assert (['gpasswd', '--add', 'frank', 'sudo'], None) in system.calls
    assert manager.root['frank'].permissions == {UserPerms.power}
    assert osusers.is_admin('frank')


def test_change_perms_revokes_admin_through_sudo(system, users_file):
    manager = UserManager({'alice': {'permissions': []}})
    manager.change_perms('alice', {UserPerms.scheduler})
    assert (['gpasswd', '--delete', 'alice', 'sudo'], None) in system.calls
    assert not osusers.is_admin('alice')
    assert manager.root['alice'].permissions == {UserPerms.scheduler}


def test_change_perms_without_admin_change_does_not_touch_sudo(system, users_file):
    manager = UserManager({})
    manager.change_perms('alice', {UserPerms.admin, UserPerms.audio})
    assert system.calls == []
    assert manager.root['alice'].permissions == {UserPerms.audio}


def test_change_perms_refused_stores_nothing(system, users_file):
    system.fail.add('gpasswd')
    manager = UserManager({'frank': {'permissions': [UserPerms.audio]}})
    with pytest.raises(osusers.OSUserError):
        manager.change_perms('frank', {UserPerms.admin})
    assert manager.root['frank'].permissions == {UserPerms.audio}
    assert not osusers.is_admin('frank')


def test_revoked_sessions_stop_working_except_the_kept_one(system, users_file):
    import time
    manager = UserManager({'alice': {'permissions': [UserPerms.scheduler]}})
    issued = 1000.0
    assert manager.session_valid('alice', 'old', issued)             # nothing revoked yet
    manager.revoke_sessions('alice', keep_sid='mine')
    assert not manager.session_valid('alice', 'old', issued)          # another session, issued before
    assert manager.session_valid('alice', 'mine', issued)             # the one the password was changed from
    assert manager.session_valid('alice', 'new', time.time() + 1)     # a login after the change
    manager.revoke_sessions('alice')                                   # an administrator changed it: nobody is kept
    assert not manager.session_valid('alice', 'mine', issued)
    assert manager.session_valid('nobody', 'x', issued)               # no record, nothing to revoke
