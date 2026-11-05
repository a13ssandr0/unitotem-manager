"""Admin is the membership of the group sudo, not a stored permission: the model reads it from the system, stores
capabilities only, and changes the system first."""
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api import security
from api.ws.responses import WSResponse
from api.ws.wsmanager import Context
from utils import osusers
from utils.models.command_line import cmdargs
from utils.models.user import UserData, UserManager, UserPerms


@pytest.fixture
def users_file(monkeypatch, tmp_path):
    # the instance attribute: a patch on the class is shadowed by it, and a save would reach /etc/unitotem
    path = tmp_path / 'users.json'
    monkeypatch.setitem(cmdargs.__dict__, 'users_file', path)
    assert cmdargs.users_file == path
    return path


@pytest.fixture
def system(monkeypatch):
    """Fake accounts: alice and bob may log in, bob is an administrator; set_admin and create_user are recorded."""
    state = SimpleNamespace(accounts={'alice', 'bob'}, admins={'bob'}, calls=[], fail_set_admin=None)

    def may_login(name):
        return name in state.accounts

    def set_admin(name, admin):
        state.calls.append(('set_admin', name, admin))
        if state.fail_set_admin is not None:
            raise state.fail_set_admin
        (state.admins.add if admin else state.admins.discard)(name)

    def create_user(name, password, hashed=False, admin=False):
        state.calls.append(('create_user', name, admin))

    monkeypatch.setattr(osusers, 'may_login', may_login)
    monkeypatch.setattr(osusers, 'is_admin', lambda name: name in state.admins)
    monkeypatch.setattr(osusers, 'exists', lambda name: name in state.accounts)
    monkeypatch.setattr(osusers, 'members', lambda: sorted(state.accounts))
    monkeypatch.setattr(osusers, 'set_admin', set_admin)
    monkeypatch.setattr(osusers, 'create_user', create_user)
    monkeypatch.setattr(osusers, 'admin_exists', lambda: any(may_login(n) and n in state.admins for n in state.accounts))
    return state


def test_admin_is_dropped_from_the_stored_permissions():
    data = UserData(permissions={UserPerms.admin, UserPerms.audio})
    assert data.permissions == {UserPerms.audio}
    assert 'admin' not in data.model_dump_json()


def test_the_stored_record_takes_no_admin_from_a_legacy_file():
    manager = UserManager(**{'alice': {'permissions': ['admin', 'scheduler'], 'password': 'x'}})
    assert manager['alice'].permissions == {UserPerms.scheduler}


def test_perms_are_stored_union_admin_from_the_system(system, users_file):
    manager = UserManager(**{'bob': {'permissions': ['audio']}, 'alice': {'permissions': ['audio']}})
    assert manager.get_user('bob').perms == {UserPerms.audio, UserPerms.admin}
    assert manager.get_user('alice').perms == {UserPerms.audio}


def test_root_is_never_a_user(system, users_file, monkeypatch):
    monkeypatch.setattr(osusers, 'may_login', lambda name: name != 'root' and name in system.accounts)
    manager = UserManager(root={'root': {'permissions': ['audio']}})
    assert manager.get_user('root') is None


def test_has_admin_asks_the_system(monkeypatch):
    manager = UserManager()
    monkeypatch.setattr(osusers, 'admin_exists', lambda: True)
    assert manager.has_admin() is True
    monkeypatch.setattr(osusers, 'admin_exists', lambda: False)
    assert manager.has_admin() is False


def test_add_user_as_admin_creates_it_in_sudo_and_stores_no_admin(system, users_file):
    manager = UserManager()
    manager.add_user('carol', 'pw-carol-1', {UserPerms.admin, UserPerms.audio})
    assert system.calls == [('create_user', 'carol', True)]
    stored = json.loads(users_file.read_text())
    assert stored['carol']['permissions'] == ['audio']
    assert 'admin' not in users_file.read_text()


def test_add_user_refused_by_the_system_stores_nothing(system, users_file, monkeypatch):
    def refuse(name, password, hashed=False, admin=False):
        raise osusers.OSUserError('No')
    monkeypatch.setattr(osusers, 'create_user', refuse)
    manager = UserManager()
    with pytest.raises(osusers.OSUserError):
        manager.add_user('carol', 'pw-carol-1', {UserPerms.audio})
    assert 'carol' not in manager.root
    assert not users_file.exists()


def test_grant_admin_changes_the_system_once(system, users_file):
    manager = UserManager(**{'alice': {'permissions': ['audio']}})
    manager.change_perms('alice', {UserPerms.admin, UserPerms.audio})
    assert system.calls == [('set_admin', 'alice', True)]
    assert manager.root['alice'].permissions == {UserPerms.audio}


def test_revoke_admin_changes_the_system_once(system, users_file):
    manager = UserManager(**{'bob': {'permissions': ['audio']}})
    manager.change_perms('bob', {UserPerms.audio})
    assert system.calls == [('set_admin', 'bob', False)]


def test_no_change_of_the_flag_leaves_the_system_alone(system, users_file):
    manager = UserManager(**{'bob': {'permissions': ['audio']}})
    manager.change_perms('bob', {UserPerms.admin, UserPerms.power})      # already an administrator
    manager.change_perms('alice', {UserPerms.audio})                     # not an administrator, stays one
    assert system.calls == []


def test_a_refused_system_change_stores_nothing(system, users_file):
    system.fail_set_admin = osusers.OSUserError('The root account is not managed by UniTotem')
    manager = UserManager(**{'alice': {'permissions': ['audio']}})
    with pytest.raises(osusers.OSUserError):
        manager.change_perms('alice', {UserPerms.admin, UserPerms.scheduler})
    assert manager.root['alice'].permissions == {UserPerms.audio}
    assert not users_file.exists()


def test_an_admin_cannot_remove_their_own_admin_role(system, monkeypatch):
    changed = []
    class FakeManager:
        def __contains__(self, name):
            return True

        def change_perms(self, name, perms):
            changed.append((name, perms))

        def admins(self):
            return ['bob']

        def items(self):
            return []

    monkeypatch.setattr(security, 'user_manager', FakeManager())
    responses = list(security.Security(None, None).set_user_permissions(Context('bob'), 'bob',
                                                            {UserPerms.audio}))
    assert isinstance(responses[0], WSResponse) and responses[0].kwargs['error'].startswith('An admin cannot')
    assert changed == []


def test_users_json_never_contains_admin(system, users_file):
    manager = UserManager(**{'alice': {'permissions': ['audio']}})
    manager.change_perms('alice', {UserPerms.admin, UserPerms.scheduler})
    manager.change_perms('alice', {UserPerms.admin})
    manager.add_user('dave', 'pw-dave-1', {UserPerms.admin})
    for entry in json.loads(users_file.read_text()).values():
        assert 'admin' not in entry['permissions']
    assert 'admin' not in users_file.read_text()
