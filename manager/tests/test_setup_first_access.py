"""First access over the web: POST /setup creates the first administrator through the one shared path, and closes the
window in order. The system is never touched: the accounts, the first-access marker and the lock are fakes."""
import fcntl
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import login, setup
from utils import first_access, osusers, passwords


class Env:
    """What the fakes record: `calls` keeps the order of the account changes."""

    def __init__(self):
        self.calls = []
        self.admin_exists = False           # the state the setup page and first_access.create_first_admin see
        self.create_error = None            # an OSUserError to raise from create_user

    def create_user(self, name, password, hashed=False, admin=False):
        self.calls.append(('create_user', name, password, admin))
        if self.create_error is not None:
            raise self.create_error

    def close_first_access(self):
        self.calls.append(('close_first_access',))


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake = Env()
    monkeypatch.setattr(first_access, 'LOCK_PATH', str(tmp_path / 'run' / 'first-admin.lock'))
    monkeypatch.setattr(osusers, 'admin_exists', lambda: fake.admin_exists)
    monkeypatch.setattr(osusers, 'create_user', fake.create_user)
    monkeypatch.setattr(osusers, 'close_first_access', fake.close_first_access)
    monkeypatch.setattr(setup, 'user_manager', SimpleNamespace(has_admin=lambda: fake.admin_exists))
    monkeypatch.setattr(setup, 'WebviewManager', SimpleNamespace(_instance=None))
    app = FastAPI()
    app.include_router(setup.router)
    fake.client = TestClient(app, follow_redirects=False)
    return fake


def post(env, username='boss', password='correct-horse-9'):
    return env.client.post('/setup', json={'username': username, 'password': password})


def test_setup_creates_the_administrator_then_closes_the_window(env):
    r = post(env)
    assert r.status_code == 200 and r.json() == {'ok': True}
    assert env.calls == [('create_user', 'boss', 'correct-horse-9', True), ('close_first_access',)]


def test_setup_answers_with_the_session_cookie(env):
    r = post(env)
    assert login.LOGMAN.cookie_name in r.headers['set-cookie']
    assert 'httponly' in r.headers['set-cookie'].lower()


def test_setup_refused_when_an_administrator_exists(env):
    env.admin_exists = True
    assert post(env).status_code == 404
    assert env.calls == []


def test_setup_busy_when_another_setup_holds_the_lock(env, monkeypatch, tmp_path):
    # a second open file description holds the lock: flock conflicts even inside one process
    lock = tmp_path / 'run' / 'first-admin.lock'
    lock.parent.mkdir(parents=True)
    holder = open(lock, 'w')
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        monkeypatch.setattr(first_access, 'LOCK_TIMEOUT', 0.2)
        r = post(env)
    finally:
        holder.close()
    assert r.status_code == 409
    assert 'Another first-access setup is running' in r.json()['detail'][0]
    assert env.calls == []


def test_setup_refuses_when_the_system_refuses_the_account(env):
    env.create_error = osusers.OSUserError('An account with this name already exists on the system')
    r = post(env)
    assert r.status_code == 409
    assert r.json() == {'detail': ['An account with this name already exists on the system']}
    assert env.calls == [('create_user', 'boss', 'correct-horse-9', True)]     # the window stays open


@pytest.mark.parametrize('password', ['', 'new\nline', 'x' * (passwords.POLICY['max_length'] + 1)])
def test_weak_password_is_refused_with_nothing_created(env, password):
    r = post(env, password=password)         # refused by the basic rules
    assert r.status_code == 422
    assert r.json()['detail']
    assert env.calls == []


def test_weak_password_is_refused_by_the_policy_with_a_message_list(env):
    r = post(env, password='aaaaaaaa')      # long enough, but one character class
    assert r.status_code == 422
    assert r.json() == {'detail': passwords.validate_password('aaaaaaaa', 'boss')}
    assert env.calls == []


def test_setup_page_state_carries_the_password_policy(env, monkeypatch):
    captured = {}

    def fake_response(request, name, context):
        captured.update(context)
        return 'page'
    monkeypatch.setattr(setup, 'templates', SimpleNamespace(TemplateResponse=fake_response))
    assets = lambda *a, **k: {'js': ['setup.js'], 'css': []}
    assets.cache_clear = lambda: None
    monkeypatch.setattr(setup, 'get_vite_assets', assets)
    monkeypatch.setattr(setup, 'load_vite_manifest', SimpleNamespace(cache_clear=lambda: None))
    monkeypatch.setattr(setup, 'is_vite_dev', lambda request: False)
    monkeypatch.setattr(setup, 'reachable_ipv4', lambda: '192.0.2.10')
    r = env.client.get('/setup')
    assert r.status_code == 200
    state = captured['state']
    assert state['rules'] == passwords.rules_spec()
    assert not {'min_length', 'max_length'} & set(state)


def test_invalid_user_name_is_refused_with_nothing_created(env):
    assert post(env, username='Boss').status_code == 422
    assert post(env, username='').status_code == 422
    assert env.calls == []


def test_setup_page_is_not_found_once_an_administrator_exists(env):
    env.admin_exists = True
    assert env.client.get('/setup').status_code == 404
