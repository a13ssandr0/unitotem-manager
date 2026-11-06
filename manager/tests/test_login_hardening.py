"""Login page hardening: redirect target, rate limits, user list switch, password in the body, setup policy, who sees accounts."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import login, setup
from utils import first_access, passwords
from utils.models.security import SecuritySettings
from utils.models.user import User, UserPerms


@pytest.mark.parametrize('src,expected', [
    ('/', '/'), ('/settings/users?x=1', '/settings/users?x=1'), (None, '/'), ('', '/'),
    ('//evil.example', '/'), ('/\\evil.example', '/'), ('https://evil.example', '/'), ('javascript:alert(1)', '/'),
    ('evil', '/'), ('/a\r\nSet-Cookie: x=1', '/'), ('/' + 'a' * 3000, '/'),
])
def test_safe_src(src, expected):
    assert login.safe_src(src) == expected


class FakeSetupManager:
    """Stands in for the user manager of a node without an administrator; records what setup stores."""

    def __init__(self):
        self.stored = {}

    def has_admin(self):
        return False

    def __setitem__(self, username, value):
        self.stored[username] = value


@pytest.fixture
def client(monkeypatch):
    settings = SecuritySettings()
    monkeypatch.setattr(login, 'security_settings', settings)
    monkeypatch.setattr('utils.models.security.security_settings', settings)
    # plain stand-in: the pydantic user manager refuses instance attributes that are not fields
    fake = SimpleNamespace(users=lambda: ['alice', 'bob'],
                           authenticate=lambda u, p: (u, p) == ('alice', 'right'),
                           has_admin=lambda: True)
    monkeypatch.setattr(login, 'user_manager', fake)
    login._login_limiter._events.clear()
    login._users_limiter._events.clear()
    app = FastAPI()
    app.include_router(login.router)
    c = TestClient(app, follow_redirects=False)
    c.settings = settings
    return c


def test_user_list_shown_by_default_and_hidden_by_setting(client):
    assert client.get('/login/users').json() == ['alice', 'bob']
    client.settings.show_login_users = False
    assert client.get('/login/users').json() == []


def test_user_list_is_rate_limited(client):
    codes = [client.get('/login/users').status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_login_wrong_password_is_401_and_attempts_are_limited(client):
    for _ in range(10):
        assert client.post('/auth/token', data={'username': 'alice', 'password': 'bad'}).status_code == 401
    # even the right password is refused once the limit is hit
    assert client.post('/auth/token', data={'username': 'alice', 'password': 'right'}).status_code == 429


def test_login_redirects_only_to_local_paths(client):
    ok = client.post('/auth/token', data={'username': 'alice', 'password': 'right', 'src': '/settings'})
    assert ok.status_code == 303 and ok.headers['location'] == '/settings'
    evil = client.post('/auth/token', data={'username': 'alice', 'password': 'right', 'src': '//evil.example'})
    assert evil.headers['location'] == '/'


def test_login_page_redirect_of_a_logged_in_session_is_checked(client, monkeypatch):
    async def ok(request):
        return object()
    monkeypatch.setattr(login, 'LOGMAN', ok)
    assert client.get('/login', params={'src': 'https://evil.example'}).headers['location'] == '/'
    assert client.get('/login', params={'src': '/scheduler'}).headers['location'] == '/scheduler'


def test_set_passwd_takes_the_password_from_the_body(monkeypatch):
    changed = []
    fake = SimpleNamespace(change_password=lambda u, p: changed.append((u, p)),
                           revoke_sessions=lambda *a, **k: None)
    monkeypatch.setattr(login, 'user_manager', fake)
    app = FastAPI()
    app.include_router(login.router)
    app.dependency_overrides[login.LOGMAN] = lambda: User(name='alice', perms=set(), sid='s')
    c = TestClient(app)
    assert c.post('/api/settings/set_passwd', params={'password': 'longenough-1'}).status_code == 422
    assert changed == []
    r = c.post('/api/settings/set_passwd', json={'password': 'longenough-1'})
    assert r.status_code == 200 and changed == [('alice', 'longenough-1')]
    weak = c.post('/api/settings/set_passwd', json={'password': 'a'})
    assert weak.status_code == 422
    assert weak.json()['detail'] == passwords.validate_password('a', 'alice')
    assert changed == [('alice', 'longenough-1')]


def _setup_client(monkeypatch, tmp_path):
    created = []
    monkeypatch.setattr(setup, 'user_manager', FakeSetupManager())
    # the real system is never touched: no accounts, no lock under /run, no sshd marker change
    monkeypatch.setattr(first_access, 'LOCK_PATH', str(tmp_path / 'first-admin.lock'))
    monkeypatch.setattr(setup.osusers, 'admin_exists', lambda: False)
    monkeypatch.setattr(setup.osusers, 'create_user', lambda u, p, admin=False: created.append((u, p)))
    monkeypatch.setattr(setup.osusers, 'close_first_access', lambda: None)
    monkeypatch.setattr(setup, 'WebviewManager', type('W', (), {'_instance': None}))
    monkeypatch.setattr(setup, 'session_cookie', lambda *a, **k: None)
    app = FastAPI()
    app.include_router(setup.router)
    return TestClient(app), created


def test_setup_always_applies_the_policy(monkeypatch, tmp_path):
    c, created = _setup_client(monkeypatch, tmp_path)
    r = c.post('/setup', json={'username': 'boss', 'password': 'aaaaaaaa'})      # too few character classes
    assert r.status_code == 422 and created == []
    assert r.json()['detail'] == passwords.validate_password('aaaaaaaa', 'boss')
    assert r.json()['detail'] and all(isinstance(m, str) for m in r.json()['detail'])


def test_setup_refuses_a_password_containing_the_username(monkeypatch, tmp_path):
    c, created = _setup_client(monkeypatch, tmp_path)
    r = c.post('/setup', json={'username': 'boss', 'password': 'the-BOSS-1'})
    assert r.status_code == 422 and created == []
    assert r.json()['detail'] == passwords.validate_password('the-BOSS-1', 'boss')
    assert passwords.failed_ids('the-BOSS-1', 'boss') == ['username']


def test_setup_accepts_a_strong_password(monkeypatch, tmp_path):
    c, created = _setup_client(monkeypatch, tmp_path)
    assert c.post('/setup', json={'username': 'boss', 'password': 'Aa1-strong'}).status_code == 200
    assert created == [('boss', 'Aa1-strong')]


def test_accounts_reach_administrators_only(monkeypatch):
    from api import security
    from api.ws.responses import WSMulticast
    from utils.models.user import Account
    data = {'root': Account('root', {UserPerms.admin}), 'bob': Account('bob', {UserPerms.audio})}
    monkeypatch.setattr(security, 'user_manager', SimpleNamespace(items=lambda: list(data.items()),
                                                                admins=lambda: ['root']))

    async def run():
        return security.Security.users()
    out = asyncio.new_event_loop().run_until_complete(run())
    assert isinstance(out, WSMulticast) and out.users == ['root']
    assert set(out.kwargs['users']) == {'root', 'bob'}
    assert security.Security.login_user_list().users == ['root']


def test_websocket_errors_carry_no_traceback():
    import inspect
    from routers.websocket import web_ui
    src = inspect.getsource(web_ui.websocket_endpoint)
    assert 'format_exc()' in src and "extra=format_exc" not in src


def test_root_cannot_log_in_to_the_web(monkeypatch):
    # root is a member of the group users here, and PAM would accept its password: the web still refuses it
    import grp
    import pwd
    import pam
    from utils import osusers
    root = pwd.struct_passwd(('root', 'x', 0, 0, 'root', '/root', '/bin/bash'))

    def getpwnam(name):
        if name != 'root':
            raise KeyError(name)
        return root
    monkeypatch.setattr(osusers.pwd, 'getpwnam', getpwnam)
    monkeypatch.setattr(osusers.grp, 'getgrnam', lambda name: grp.struct_group(('users', 'x', 100, ['root'])))
    monkeypatch.setattr(pam.pam, 'authenticate', lambda *a, **k: pytest.fail('PAM asked about root'))
    monkeypatch.setattr(login, 'user_manager', SimpleNamespace(users=lambda: [], authenticate=osusers.authenticate,
                                                               has_admin=lambda: True))
    login._login_limiter._events.clear()
    app = FastAPI()
    app.include_router(login.router)
    c = TestClient(app, follow_redirects=False)
    assert c.post('/auth/token', data={'username': 'root', 'password': 'right'}).status_code == 401
