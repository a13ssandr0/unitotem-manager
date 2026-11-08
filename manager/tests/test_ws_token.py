"""
The one-time tokens of the websockets that give a window onto the machine: issue, validation, expiry, replay, the
account and session binding, the scope of a page, and the first frame of a websocket. Also the Origin check.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from plugins.unitotem_node import ws_token
from plugins.unitotem_node.ws_token import new_token, read_token, same_origin, take_token


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock the test moves by hand; the tokens table starts empty."""
    now = [1000.0]
    monkeypatch.setattr(ws_token, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(ws_token, '_tokens', {})
    return now


class FakeSocket:
    def __init__(self, frames=(), headers=None, error=None):
        self.headers = headers or {}
        self.frames = list(frames)
        self.error = error
        self.closed = []

    async def receive_text(self):
        if self.error is not None:
            raise self.error
        return self.frames.pop(0)

    async def close(self, code, reason=''):
        self.closed.append((code, reason))


def run(coro):
    return asyncio.run(coro)


# ── issue and spend ──────────────────────────────────────────────────────────

def test_a_token_is_long_url_safe_and_unique(clock):
    tokens = {new_token('admin', 'sid1') for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 43 and set(t) <= set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_') for t in tokens)


def test_a_token_opens_once_for_its_account_and_session(clock):
    token = new_token('admin', 'sid1')
    assert take_token(token, 'admin', 'sid1') is True
    assert take_token(token, 'admin', 'sid1') is False          # replay: spent by the first look


def test_an_unknown_token_is_refused(clock):
    assert take_token('not-issued', 'admin', 'sid1') is False
    assert take_token('', 'admin', None) is False


def test_a_token_is_spent_even_when_the_account_is_wrong(clock):
    token = new_token('admin', 'sid1')
    assert take_token(token, 'mallory', 'sid1') is False
    assert take_token(token, 'admin', 'sid1') is False          # the wrong try used it up


def test_a_token_is_spent_even_when_the_session_is_wrong(clock):
    token = new_token('admin', 'sid1')
    assert take_token(token, 'admin', 'sid2') is False
    assert take_token(token, 'admin', 'sid1') is False


def test_a_token_is_bound_to_the_session_that_asked_for_it(clock):
    token = new_token('admin', None)
    assert take_token(token, 'admin', 'sid1') is False          # no session id on the token: another session cannot use it
    token = new_token('admin', None)
    assert take_token(token, 'admin', None) is True


def test_a_token_is_bound_to_its_scope(clock):
    token = new_token('admin', 'sid1')
    assert take_token(token, 'admin', 'sid1', scope='A' * 32) is False       # a scoped request does not take a plain token
    token = new_token('admin', 'sid1', scope='A' * 32)
    assert take_token(token, 'admin', 'sid1') is False                       # a plain request does not take a scoped token
    token = new_token('admin', 'sid1', scope='A' * 32)
    assert take_token(token, 'admin', 'sid1', scope='B' * 32) is False       # another page
    token = new_token('admin', 'sid1', scope='A' * 32)
    assert take_token(token, 'admin', 'sid1', scope='A' * 32) is True


def test_tokens_of_two_accounts_do_not_mix(clock):
    a = new_token('alice', 's')
    b = new_token('bob', 's')
    assert take_token(a, 'bob', 's') is False     # alice's token, tried by bob: refused and spent
    assert take_token(b, 'bob', 's') is True      # bob's own token still works
    assert take_token(a, 'alice', 's') is False


# ── expiry ───────────────────────────────────────────────────────────────────

def test_a_token_is_valid_for_the_ttl_and_no_longer(clock):
    token = new_token('admin', 'sid1')
    clock[0] += ws_token.TOKEN_TTL - 0.5
    assert take_token(token, 'admin', 'sid1') is True                # just inside the TTL


def test_an_expired_token_is_refused(clock):
    token = new_token('admin', 'sid1')
    clock[0] += ws_token.TOKEN_TTL + 0.001
    assert take_token(token, 'admin', 'sid1') is False


def test_the_expired_tokens_are_forgotten_when_a_new_one_is_issued(clock):
    old = [new_token('admin', 's') for _ in range(5)]
    assert len(ws_token._tokens) == 5
    clock[0] += ws_token.TOKEN_TTL + 1
    new_token('admin', 's')
    assert len(ws_token._tokens) == 1
    assert all(t not in ws_token._tokens for t in old)


# ── the first frame of a websocket ───────────────────────────────────────────

def test_a_websocket_opens_with_the_token_in_its_first_frame(clock):
    token = new_token('admin', 'sid1')
    ws = FakeSocket([json.dumps({'token': token})])
    assert run(read_token(ws, 'admin', 'sid1')) is True
    assert take_token(token, 'admin', 'sid1') is False               # read_token spent it


def test_a_websocket_is_refused_with_a_wrong_or_used_token(clock):
    token = new_token('admin', 'sid1')
    assert run(read_token(FakeSocket([json.dumps({'token': 'nope'})]), 'admin', 'sid1')) is False
    assert run(read_token(FakeSocket([json.dumps({'token': token})]), 'admin', 'sid1')) is True
    assert run(read_token(FakeSocket([json.dumps({'token': token})]), 'admin', 'sid1')) is False   # replayed on a new socket


@pytest.mark.parametrize('frame', [
    'not json',
    '{"token": 5}',
    '{"token": null}',
    '{"other": "x"}',
    '[1, 2]',
    '"just a string"',
    '{"token": ["x"]}',
])
def test_a_malformed_first_frame_is_refused(clock, frame):
    assert run(read_token(FakeSocket([frame]), 'admin', 'sid1')) is False


def test_a_scoped_token_cannot_open_a_plain_websocket(clock):
    token = new_token('admin', 'sid1', scope='A' * 32)
    assert run(read_token(FakeSocket([json.dumps({'token': token})]), 'admin', 'sid1')) is False


def test_a_websocket_that_sends_nothing_is_refused_after_the_ttl(clock, monkeypatch):
    monkeypatch.setattr(ws_token, 'TOKEN_TTL', 0.05)

    class Silent(FakeSocket):
        async def receive_text(self):
            await asyncio.sleep(10)

    assert run(read_token(Silent(), 'admin', 'sid1')) is False


def test_a_websocket_that_disconnects_before_the_token_is_refused(clock):
    ws = FakeSocket(error=WebSocketDisconnect(1001))
    assert run(read_token(ws, 'admin', 'sid1')) is False


def test_a_websocket_in_the_wrong_state_is_refused(clock):
    ws = FakeSocket(error=RuntimeError('Cannot call "receive" once a disconnect message has been received.'))
    assert run(read_token(ws, 'admin', 'sid1')) is False


# ── the Origin of the websocket ──────────────────────────────────────────────

def test_a_websocket_without_an_origin_is_not_a_browser_page_of_another_site():
    assert same_origin(SimpleNamespace(headers={'host': 'totem.local'})) is True


def test_the_origin_must_be_this_host_with_the_same_port():
    ok = SimpleNamespace(headers={'origin': 'https://totem.local', 'host': 'totem.local'})
    assert same_origin(ok) is True
    with_port = SimpleNamespace(headers={'origin': 'https://totem.local:8443', 'host': 'totem.local:8443'})
    assert same_origin(with_port) is True
    other_port = SimpleNamespace(headers={'origin': 'https://totem.local:8443', 'host': 'totem.local'})
    assert same_origin(other_port) is False


@pytest.mark.parametrize('origin', [
    'https://evil.example',
    'https://totem.local.evil.example',
    'https://evil.example/totem.local',
    'null',
])
def test_a_foreign_origin_is_refused(origin):
    assert same_origin(SimpleNamespace(headers={'origin': origin, 'host': 'totem.local'})) is False
