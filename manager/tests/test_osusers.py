"""The web interface's accounts are the system's: group check, PAM, creation, backup of the password hash."""
import grp
import pwd
from types import SimpleNamespace

import pytest

from utils import osusers
from utils.models.user import UserManager, UserPerms

GID = 990


def account(name, uid, gid=1000, gecos=''):
    return pwd.struct_passwd((name, 'x', uid, gid, gecos, '/', '/usr/sbin/nologin'))


@pytest.fixture
def system(monkeypatch):
    """A fake /etc/passwd and /etc/group: root, alice (member), bob (not a member), carol (primary group unitotem)."""
    accounts = {'root': account('root', 0, 0), 'alice': account('alice', 1001, gecos=osusers.GECOS),
                'bob': account('bob', 1002), 'carol': account('carol', 1003, GID)}
    group = grp.struct_group((osusers.GROUP, 'x', GID, ['alice']))

    def getpwnam(name):
        try:
            return accounts[name]
        except KeyError:
            raise KeyError(name) from None

    monkeypatch.setattr(osusers.pwd, 'getpwnam', getpwnam)
    monkeypatch.setattr(osusers.pwd, 'getpwall', lambda: list(accounts.values()))
    monkeypatch.setattr(osusers.grp, 'getgrnam', lambda name: group)
    monkeypatch.setattr(osusers.grp, 'getgrgid', lambda gid: group)
    calls = []
    monkeypatch.setattr(osusers, '_run', lambda args, secret=None: calls.append((args, secret)))
    return SimpleNamespace(accounts=accounts, calls=calls)


def test_only_root_and_members_may_log_in(system):
    assert osusers.may_login('root')
    assert osusers.may_login('alice')          # supplementary member
    assert osusers.may_login('carol')          # primary group
    assert not osusers.may_login('bob')
    assert not osusers.may_login('nobody-here')
    assert not osusers.may_login(None)


def test_members_exclude_root(system):
    assert osusers.members() == ['alice', 'carol']


def test_a_non_member_never_reaches_pam(system, monkeypatch):
    import pam
    monkeypatch.setattr(pam.pam, 'authenticate', lambda *a, **k: pytest.fail('PAM asked about a non-member'))
    assert osusers.authenticate('bob', 'whatever') is False


def test_member_is_checked_by_pam(system, monkeypatch):
    import pam
    seen = []
    monkeypatch.setattr(pam.pam, 'authenticate', lambda self, name, password, service: seen.append(service) or password == 'right')
    assert osusers.authenticate('alice', 'right') is True
    assert osusers.authenticate('alice', 'wrong') is False
    assert osusers.authenticate('alice', 'x' * 600) is False
    assert len(seen) == 2


def test_create_user_runs_useradd_then_chpasswd_with_the_secret_on_stdin(system):
    osusers.create_user('dave', 'pw-1234567890')
    useradd, chpasswd = system.calls
    assert useradd[0][0] == 'useradd' and '--groups' in useradd[0] and osusers.GROUP in useradd[0]
    assert 'pw-1234567890' not in ' '.join(useradd[0])
    assert chpasswd == (['chpasswd'], 'dave:pw-1234567890\n')


@pytest.mark.parametrize('name', ['', 'Dave', '1dave', 'da ve', 'dave:x', 'a' * 33, 'dave\n'])
def test_bad_names_are_refused(system, name):
    with pytest.raises(osusers.OSUserError):
        osusers.create_user(name, 'pw-1234567890')
    assert system.calls == []


def test_an_existing_system_account_is_never_taken_over(system):
    with pytest.raises(osusers.OSUserError):
        osusers.create_user('bob', 'pw-1234567890')


def test_password_with_newline_cannot_smuggle_another_chpasswd_line(system):
    with pytest.raises(osusers.OSUserError):
        osusers.set_password('alice', 'x\nroot:y')


def test_only_accounts_made_by_unitotem_are_deleted(system):
    osusers.remove_user('alice')               # made by UniTotem
    assert system.calls[-1][0] == ['userdel', 'alice']
    system.calls.clear()
    osusers.remove_user('carol')               # a real account: out of the group at most, never deleted
    assert all(args[0] != 'userdel' for args, _ in system.calls)
    osusers.remove_user('root')
    assert all('root' not in args for args, _ in system.calls)


def test_restore_creates_with_the_encrypted_hash(system):
    osusers.restore_user('erin', '$y$j9T$salt$hash')
    assert system.calls[-1] == (['chpasswd', '--encrypted'], 'erin:$y$j9T$salt$hash\n')
    system.calls.clear()
    osusers.restore_user('bob', '$y$j9T$salt$hash')          # exists: joins the group, gets its password back
    assert system.calls[0][0][:2] == ['gpasswd', '--add']


def test_password_hash_is_read_from_shadow(tmp_path, monkeypatch):
    shadow = tmp_path / 'shadow'
    shadow.write_text('root:!:1:::::\nalice:$y$abc:1:::::\nlocked::1:::::\n')
    monkeypatch.setattr(osusers, 'SHADOW', shadow)
    assert osusers.password_hash('alice') == '$y$abc'
    assert osusers.password_hash('locked') is None
    assert osusers.password_hash('nobody') is None


def test_user_manager_lists_members_and_has_admin(system, monkeypatch, tmp_path):
    from utils.models import user as user_module
    monkeypatch.setattr(user_module.cmdargs.__class__, 'users_file', tmp_path / 'users.json', raising=False)
    manager = UserManager({'alice': {'permissions': [UserPerms.scheduler]}, 'ghost': {'permissions': [UserPerms.admin]}})
    assert list(manager.users()) == ['alice', 'carol']            # 'ghost' has no system account
    assert not manager.has_admin()
    assert manager.get_user('bob') is None                       # not in the group: no session
    assert manager.get_user('carol').perms == set()              # in the group but never given a capability
    assert manager.get_user('root').perms == {UserPerms.admin}
    manager.root['alice'].permissions = {UserPerms.admin}
    assert manager.has_admin()


def test_revoked_sessions_stop_working_except_the_kept_one(system, monkeypatch, tmp_path):
    from utils.models import user as user_module
    monkeypatch.setattr(user_module.cmdargs.__class__, 'users_file', tmp_path / 'users.json', raising=False)
    manager = UserManager({'alice': {'permissions': [UserPerms.scheduler]}})
    issued = 1000.0
    assert manager.session_valid('alice', 'old', issued)             # nothing revoked yet
    manager.revoke_sessions('alice', keep_sid='mine')
    assert not manager.session_valid('alice', 'old', issued)          # another session, issued before
    assert manager.session_valid('alice', 'mine', issued)             # the one the password was changed from
    import time
    assert manager.session_valid('alice', 'new', time.time() + 1)     # a login after the change
    manager.revoke_sessions('alice')                                   # an administrator changed it: nobody is kept
    assert not manager.session_valid('alice', 'mine', issued)
    assert manager.session_valid('nobody', 'x', issued)               # no record, nothing to revoke
