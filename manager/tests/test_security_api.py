"""The password policy over the web API: one policy for every path that sets a password. The user manager is a fake:
no account is created or changed."""
from types import SimpleNamespace

import pytest

from api import security
from api.security import Security
from api.ws.responses import WSBroadcast, WSMulticast, WSResponse
from api.ws.wsmanager import Context
from utils import passwords


class FakeManager:
    """Records the account changes; `existing` are the accounts that already exist."""

    def __init__(self, existing=('root', 'bob')):
        self.existing = set(existing)
        self.added = []
        self.changed = []
        self.revoked = []

    def __contains__(self, name):
        return name in self.existing

    def add_user(self, username, password, permissions=None):
        self.added.append((username, password))

    def change_password(self, username, password):
        self.changed.append((username, password))

    def revoke_sessions(self, username, keep_sid=None):
        self.revoked.append((username, keep_sid))

    def admins(self):
        return ['root']

    def items(self):
        return []


@pytest.fixture
def manager(monkeypatch):
    fake = FakeManager()
    monkeypatch.setattr(security, 'user_manager', fake)
    return fake


def test_password_policy_returns_the_spec(manager):
    out = Security.password_policy()
    assert isinstance(out, WSBroadcast)
    assert out.kwargs['rules'] == passwords.rules_spec()
    assert out.kwargs['rules']['rules'][0]['id'] == 'min_length'
    assert 'enforced' not in out.kwargs


def test_set_password_policy_is_gone():
    assert not hasattr(Security, 'set_password_policy')


def test_add_user_refuses_a_weak_password(manager):
    out = Security(None, None).add_user('carol', 'aaaaaaaa')
    assert isinstance(out, WSResponse)
    assert out.kwargs['error'] == 'Password refused'
    assert out.kwargs['extra'] == passwords.validate_password('aaaaaaaa', 'carol')
    assert out.kwargs['extra'] and manager.added == []


def test_add_user_accepts_a_strong_password(manager):
    out = Security(None, None).add_user('carol', 'Aa1-strong')
    assert isinstance(out, WSMulticast)
    assert manager.added == [('carol', 'Aa1-strong')]


def test_add_user_refuses_a_password_containing_the_username(manager):
    out = Security(None, None).add_user('carol', 'the-CAROL-1')
    assert out.kwargs['extra'] == passwords.validate_password('the-CAROL-1', 'carol')
    assert passwords.failed_ids('the-CAROL-1', 'carol') == ['username']
    assert manager.added == []


def test_set_user_password_refuses_a_weak_password(manager):
    out = Security.set_user_password(Context('root'), 'bob', 'aaaaaaaa')
    assert out.kwargs['error'] == 'Password refused'
    assert out.kwargs['extra'] == passwords.validate_password('aaaaaaaa', 'bob')
    assert manager.changed == [] and manager.revoked == []


def test_set_user_password_accepts_a_strong_password(manager):
    out = Security.set_user_password(Context('root'), 'bob', 'Aa1-strong')
    assert out is None
    assert manager.changed == [('bob', 'Aa1-strong')]
    assert manager.revoked == [('bob', None)]


def test_set_user_password_keeps_the_own_session(manager):
    Security.set_user_password(Context('bob', sid='s1'), 'bob', 'Aa1-strong')
    assert manager.revoked == [('bob', 's1')]
