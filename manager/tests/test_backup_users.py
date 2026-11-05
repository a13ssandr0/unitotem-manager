"""The users part of a backup: the admin flag comes from the system's group sudo, the archive never holds 'admin' as a
permission, and restoring recreates the accounts with their password hash."""
import json
from types import SimpleNamespace

import pytest

from utils import backup, osusers, sessions
from utils.models.command_line import cmdargs
from utils.models.user import Account, UserManager, UserPerms

HASH_ALICE = '$6$salt$alicehash'
HASH_BOB = '$6$salt$bobhash'


@pytest.fixture
def users_file(monkeypatch, tmp_path):
    path = tmp_path / 'users.json'
    monkeypatch.setitem(cmdargs.__dict__, 'users_file', path)
    monkeypatch.setattr(sessions, 'close_soon', lambda *a, **k: None)
    return path


@pytest.fixture
def system(monkeypatch):
    """alice is an administrator (group sudo), bob is not; the hashes are the ones of /etc/shadow."""
    state = SimpleNamespace(admins={'alice'}, hashes={'alice': HASH_ALICE, 'bob': HASH_BOB}, restored=[], fail=set())
    monkeypatch.setattr(osusers, 'is_admin', lambda name: name in state.admins)
    monkeypatch.setattr(osusers, 'password_hash', lambda name: state.hashes.get(name))

    def restore_user(name, shadow_hash, admin=False):
        if name in state.fail:
            raise osusers.OSUserError(f'cannot restore {name}')
        state.restored.append((name, shadow_hash, admin))

    monkeypatch.setattr(osusers, 'restore_user', restore_user)
    return state


def test_export_carries_the_admin_flag_and_skips_root(monkeypatch, system):
    items = [('root', Account('root', {UserPerms.admin})),
             ('alice', Account('alice', {UserPerms.audio, UserPerms.admin})),
             ('bob', Account('bob', {UserPerms.power}))]
    monkeypatch.setattr(backup, 'user_manager', SimpleNamespace(items=lambda: items))
    exported = backup._export_users()
    assert set(exported) == {'alice', 'bob'}
    assert exported['alice'] == {'permissions': ['audio'], 'admin': True, 'shadow': HASH_ALICE}
    assert exported['bob'] == {'permissions': ['power'], 'admin': False, 'shadow': HASH_BOB}


def test_export_never_holds_the_admin_permission(monkeypatch, system):
    items = [('alice', Account('alice', {UserPerms.admin, UserPerms.scheduler}))]
    monkeypatch.setattr(backup, 'user_manager', SimpleNamespace(items=lambda: items))
    assert 'admin' not in json.dumps(backup._export_users()['alice']['permissions'])


def test_restore_recreates_the_administrator_from_the_flag(system, users_file, monkeypatch):
    monkeypatch.setattr(backup, 'user_manager', UserManager())
    backup._restore_users({'alice': {'permissions': ['audio'], 'admin': True, 'shadow': HASH_ALICE}}, 1)
    assert system.restored == [('alice', HASH_ALICE, True)]


def test_an_old_archive_marks_the_admin_in_the_permissions(system, users_file, monkeypatch):
    monkeypatch.setattr(backup, 'user_manager', UserManager())
    backup._restore_users({'bob': {'permissions': ['admin', 'power'], 'shadow': HASH_BOB}}, 1)
    assert system.restored == [('bob', HASH_BOB, True)]


def test_restore_stores_the_capabilities_without_admin(system, users_file, monkeypatch):
    manager = UserManager()
    monkeypatch.setattr(backup, 'user_manager', manager)
    backup._restore_users({'bob': {'permissions': ['admin', 'power'], 'shadow': HASH_BOB},
                           'alice': {'permissions': ['audio'], 'admin': True, 'shadow': HASH_ALICE}}, 1)
    assert manager.root['bob'].permissions == {UserPerms.power}
    assert manager.root['alice'].permissions == {UserPerms.audio}
    saved = json.loads(users_file.read_text())
    assert all('admin' not in entry['permissions'] for entry in saved.values())
    assert 'admin' not in users_file.read_text()


def test_restore_of_a_user_without_password_skips_it(system, users_file, monkeypatch):
    manager = UserManager()
    monkeypatch.setattr(backup, 'user_manager', manager)
    backup._restore_users({'bob': {'permissions': ['power']}}, 1)
    assert system.restored == []
    assert 'bob' not in manager.root


def test_a_refused_account_does_not_stop_the_others(system, users_file, monkeypatch):
    system.fail.add('bob')
    manager = UserManager()
    monkeypatch.setattr(backup, 'user_manager', manager)
    backup._restore_users({'bob': {'permissions': ['power'], 'shadow': HASH_BOB},
                           'alice': {'permissions': [], 'admin': True, 'shadow': HASH_ALICE}}, 1)
    assert system.restored == [('alice', HASH_ALICE, True)]
    assert 'bob' not in manager.root and 'alice' in manager.root


def test_export_then_restore_keeps_the_administrator(monkeypatch, system, users_file):
    items = [('alice', Account('alice', {UserPerms.audio, UserPerms.admin})),
             ('bob', Account('bob', {UserPerms.power}))]
    monkeypatch.setattr(backup, 'user_manager', SimpleNamespace(items=lambda: items))
    archive = json.loads(json.dumps(backup._export_users()))
    monkeypatch.setattr(backup, 'user_manager', UserManager())
    backup._restore_users(archive, 1)
    assert sorted(system.restored) == [('alice', HASH_ALICE, True), ('bob', HASH_BOB, False)]
