"""
The developer tools of a window: who may open them (administrator, session cookie, Origin, one-time token bound to the
account, the session and the page), which files of the viewer's endpoint are served, the protocol bridge and its
session limit. The viewer's endpoint is a fake (aiohttp and websockets are replaced); nothing listens or connects.
"""
import sys


def _real_login():
    """test_vnc installs a stand-in routers.login (no LOGMAN, no permission check). The routes of this module need the real
    one, so the plugin modules that import it are loaded again after the stand-in is replaced."""
    if 'routers.login' in sys.modules and getattr(sys.modules['routers.login'], 'LOGMAN', None) is None:
        for name in [n for n in sys.modules if n == 'routers.login' or n.startswith('plugins.unitotem_node')]:
            del sys.modules[name]
    import routers.login  # noqa: F401


_real_login()

import asyncio
import re
from types import SimpleNamespace

import aiohttp
import pytest
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from plugins.unitotem_node import devtools, ws_token
from plugins.unitotem_node.ws_token import new_token, take_token
from routers.login import NotAuthenticatedException
from utils import sessions

PAGE = 'A' * 32
OTHER_PAGE = 'B' * 32
OPEN_PATH = re.compile(r'/devtools/inspector\.html\?wss=\{host\}/devtools/ws/([A-Za-z0-9_-]+)/([0-9A-Fa-f]{32})')


# ── stand-ins ────────────────────────────────────────────────────────────────

class FakeContent:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


class FakeReply:
    def __init__(self, status=200, headers=None, chunks=(), json_body=None, json_error=None):
        self.status = status
        self.headers = headers if headers is not None else {'Content-Type': 'text/html; charset=utf-8'}
        self.content = FakeContent(chunks)
        self._json = json_body
        self._json_error = json_error

    async def json(self, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeEndpoint:
    """Stands in for aiohttp.ClientSession: `route(url)` answers each GET (a FakeReply, or an exception to raise)."""

    def __init__(self, route, calls):
        self.route = route
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.calls.append(url)
        answer = self.route(url)
        if isinstance(answer, BaseException):
            raise answer
        return answer


@pytest.fixture
def endpoint(monkeypatch):
    """The viewer's endpoint: set `endpoint.route` to what each request gets. Records the URLs asked for."""
    state = SimpleNamespace(route=lambda url: FakeReply(), calls=[])

    def session(**kwargs):
        return FakeEndpoint(lambda url: state.route(url), state.calls)

    monkeypatch.setattr(devtools.aiohttp, 'ClientSession', session)
    return state


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(ws_token, '_tokens', {})
    monkeypatch.setattr(devtools, '_sessions', 0)


def admin(name='alice', sid='s1'):
    return SimpleNamespace(name=name, sid=sid, has_perm=SimpleNamespace(admin=True))


def page(window, page_id=PAGE, url=None, kind='page'):
    return {'type': kind, 'id': page_id, 'url': url or f'file:///ui/index.html?window={window}'}


def set_targets(monkeypatch, listing):
    async def fake_targets():
        return listing
    monkeypatch.setattr(devtools, '_targets', fake_targets)


def open_window(window_id, who=None):
    return asyncio.run(devtools.devtools_open(devtools.OpenRequest(window_id=window_id), user=who or admin()))


# ── opening the tools of a window ────────────────────────────────────────────

def test_the_tools_open_at_the_page_of_the_window_with_a_token_for_that_page(monkeypatch):
    set_targets(monkeypatch, [page(0)])
    answer = open_window(0)
    match = OPEN_PATH.fullmatch(answer['path'])
    assert match, answer
    token, page_id = match.groups()
    assert page_id == PAGE
    assert take_token(token, 'alice', 's1', scope=PAGE) is True


def test_the_token_of_the_tools_opens_only_once(monkeypatch):
    set_targets(monkeypatch, [page(0)])
    token = OPEN_PATH.fullmatch(open_window(0)['path']).group(1)
    assert take_token(token, 'alice', 's1', scope=PAGE) is True
    assert take_token(token, 'alice', 's1', scope=PAGE) is False


def test_the_token_of_the_tools_is_bound_to_the_account_and_the_session(monkeypatch):
    set_targets(monkeypatch, [page(0)])
    token = OPEN_PATH.fullmatch(open_window(0)['path']).group(1)
    assert take_token(token, 'bob', 's1', scope=PAGE) is False


def test_each_window_gets_its_own_page(monkeypatch):
    set_targets(monkeypatch, [page(0, PAGE), page(1, OTHER_PAGE)])
    assert OPEN_PATH.fullmatch(open_window(1)['path']).group(2) == OTHER_PAGE
    assert OPEN_PATH.fullmatch(open_window(0)['path']).group(2) == PAGE


def test_the_window_is_found_among_other_query_parameters(monkeypatch):
    set_targets(monkeypatch, [page(0, url='file:///ui/index.html?lang=it&window=3&x=1')])
    assert OPEN_PATH.fullmatch(open_window(3)['path']).group(2) == PAGE


def test_a_window_number_is_matched_whole(monkeypatch):
    set_targets(monkeypatch, [page(0, PAGE, url='file:///ui/index.html?window=12')])
    with pytest.raises(HTTPException) as missing:
        open_window(1)
    assert missing.value.status_code == 404


def test_a_window_without_a_page_is_not_found(monkeypatch):
    set_targets(monkeypatch, [page(0, kind='background_page'), page(0, page_id='not-hex')])
    with pytest.raises(HTTPException) as missing:
        open_window(0)
    assert missing.value.status_code == 404


def test_an_unknown_window_is_not_found(monkeypatch):
    set_targets(monkeypatch, [])
    with pytest.raises(HTTPException) as missing:
        open_window(7)
    assert missing.value.status_code == 404


@pytest.mark.parametrize('failure', [aiohttp.ClientConnectionError('refused'), asyncio.TimeoutError(), ValueError('not json')])
def test_the_viewer_not_running_is_said_so(endpoint, failure):
    endpoint.route = lambda url: failure
    with pytest.raises(HTTPException) as down:
        open_window(0)
    assert down.value.status_code == 503


def test_the_list_of_pages_is_read_from_the_viewer_endpoint_only(endpoint):
    endpoint.route = lambda url: FakeReply(json_body=[page(0)])
    open_window(0)
    assert endpoint.calls == ['http://127.0.0.1:9223/json']


# ── the admin gate, through the router ──────────────────────────────────────

def _session_loader():
    """The session loader the route's permission check depends on (found on the route, so a stand-in login module
    installed by another test module cannot change it)."""
    route = next(r for r in devtools.router.routes if getattr(r, 'path', '') == '/devtools/open')
    permission = route.dependant.dependencies[0]
    return permission.dependencies[0].call


@pytest.fixture
def client(monkeypatch):
    """The real router behind the real permission check; only the session is stood in for."""
    current = {'user': None}
    app = FastAPI()
    app.include_router(devtools.router)
    app.add_exception_handler(NotAuthenticatedException, lambda request, exc: JSONResponse({}, status_code=401))
    def session():
        if current['user'] is None:
            raise NotAuthenticatedException()
        return current['user']

    app.dependency_overrides[_session_loader()] = session
    tc = TestClient(app)
    tc.current = current
    return tc


def test_an_anonymous_visitor_gets_no_tools(client, monkeypatch):
    set_targets(monkeypatch, [page(0)])
    assert client.post('/devtools/open', json={'window_id': 0}).status_code == 401
    assert client.get('/devtools/inspector.html').status_code == 401


def test_a_user_without_admin_gets_no_tools(client, monkeypatch):
    set_targets(monkeypatch, [page(0)])
    client.current['user'] = SimpleNamespace(name='bob', sid='s2', has_perm=SimpleNamespace(admin=False))
    assert client.post('/devtools/open', json={'window_id': 0}).status_code == 403
    assert client.get('/devtools/inspector.html').status_code == 403


def test_an_administrator_opens_the_tools_through_the_router(client, monkeypatch):
    set_targets(monkeypatch, [page(0)])
    client.current['user'] = admin()
    answer = client.post('/devtools/open', json={'window_id': 0})
    assert answer.status_code == 200 and OPEN_PATH.fullmatch(answer.json()['path'])


@pytest.mark.parametrize('body', [{}, {'window_id': 'x'}, {'window_id': 1.5}, {'window_id': None}])
def test_a_window_number_that_is_not_a_number_is_refused(client, body):
    client.current['user'] = admin()
    assert client.post('/devtools/open', json=body).status_code == 422


# ── the files of the front end ───────────────────────────────────────────────

def test_a_file_of_the_front_end_is_served_with_the_safety_headers(endpoint):
    endpoint.route = lambda url: FakeReply(chunks=[b'<html>', b'</html>'])
    answer = asyncio.run(devtools.devtools_file('inspector.html', user=admin()))
    assert answer.body == b'<html></html>'
    assert answer.media_type == 'text/html'
    assert answer.headers['cache-control'] == 'no-store'
    assert answer.headers['x-content-type-options'] == 'nosniff'
    assert answer.headers['x-frame-options'] == 'DENY'
    assert endpoint.calls == ['http://127.0.0.1:9223/devtools/inspector.html']


def test_a_file_without_a_type_is_served_as_bytes(endpoint):
    endpoint.route = lambda url: FakeReply(headers={}, chunks=[b'\x00\x01'])
    answer = asyncio.run(devtools.devtools_file('blob.bin', user=admin()))
    assert answer.media_type == 'application/octet-stream' and answer.body == b'\x00\x01'


@pytest.mark.parametrize('upstream, expected', [(404, 404), (403, 403), (500, 502), (301, 502)])
def test_a_file_the_endpoint_does_not_give_is_an_error_here(endpoint, upstream, expected):
    endpoint.route = lambda url: FakeReply(status=upstream)
    with pytest.raises(HTTPException) as failed:
        asyncio.run(devtools.devtools_file('missing.js', user=admin()))
    assert failed.value.status_code == expected


def test_a_file_larger_than_the_limit_is_refused(endpoint, monkeypatch):
    monkeypatch.setattr(devtools, 'MAX_FILE', 10)
    endpoint.route = lambda url: FakeReply(chunks=[b'x' * 8, b'x' * 8])
    with pytest.raises(HTTPException) as big:
        asyncio.run(devtools.devtools_file('big.js', user=admin()))
    assert big.value.status_code == 502


@pytest.mark.parametrize('failure', [aiohttp.ClientConnectionError('refused'), asyncio.TimeoutError()])
def test_a_file_while_the_viewer_is_down_is_a_503(endpoint, failure):
    endpoint.route = lambda url: failure
    with pytest.raises(HTTPException) as down:
        asyncio.run(devtools.devtools_file('inspector.html', user=admin()))
    assert down.value.status_code == 503


@pytest.mark.parametrize('path', [
    '', '..', '../etc/passwd', 'a/../b', 'a b', 'a?x=1', 'a%2e%2e/b', 'a\n', 'x' * 201,
])
def test_a_path_that_is_not_a_plain_file_name_is_not_requested(endpoint, path):
    with pytest.raises(HTTPException) as refused:
        asyncio.run(devtools.devtools_file(path, user=admin()))
    assert refused.value.status_code == 404
    assert endpoint.calls == []


@pytest.mark.parametrize('path', ['page', 'page/' + PAGE, 'browser/version', 'ws/x', 'json', 'json/list'])
def test_the_protocol_and_the_target_list_are_never_served_as_files(endpoint, path):
    with pytest.raises(HTTPException) as refused:
        asyncio.run(devtools.devtools_file(path, user=admin()))
    assert refused.value.status_code == 404
    assert endpoint.calls == []


@pytest.mark.parametrize('path', ['/page/' + PAGE, '/json', '/browser/version'])
def test_a_leading_slash_does_not_reach_the_protocol_endpoints(endpoint, path):
    with pytest.raises(HTTPException) as refused:
        asyncio.run(devtools.devtools_file(path, user=admin()))
    assert refused.value.status_code == 404
    assert endpoint.calls == []


# ── the protocol bridge ──────────────────────────────────────────────────────

class FakeBrowser:
    """The administrator's side of the websocket: `frames` are what it sends, then it disconnects."""

    def __init__(self, frames=(), user=None):
        self.frames = list(frames)
        self.headers = {}
        self.cookies = {}
        self.client = SimpleNamespace(host='10.0.0.9')
        self.query_params = {}
        self.sent = []
        self.closed = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=''):
        self.closed.append((code, reason))

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        if self.frames:
            return self.frames.pop(0)
        raise WebSocketDisconnect(1000)


class FakePage:
    """The page's protocol socket: it says one thing and then stays open until the bridge is cancelled."""

    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        yield '{"id":1}'
        await asyncio.Event().wait()


class FakeConnect:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def connect(monkeypatch):
    state = SimpleNamespace(calls=[], page=FakePage(), error=None)

    def fake_connect(url, **kwargs):
        state.calls.append((url, kwargs))
        if state.error is not None:
            raise state.error
        return FakeConnect(state.page)

    monkeypatch.setattr(devtools.websockets, 'connect', fake_connect)
    return state


def run_bridge(browser, token, page_id):
    async def go():
        await asyncio.wait_for(devtools.devtools_socket(browser, token, page_id), 5)

    return asyncio.run(go())


def authorized(monkeypatch, who):
    async def authorize(websocket):
        return who

    monkeypatch.setattr(devtools, 'authorize', authorize)


def test_a_socket_that_fails_the_admin_gate_is_closed_by_the_gate(monkeypatch, connect):
    authorized(monkeypatch, None)
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1', scope=PAGE), PAGE)
    assert browser.closed == [] and connect.calls == []


def test_the_bridge_needs_a_token_for_that_page(monkeypatch, connect):
    authorized(monkeypatch, admin())
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1', scope=OTHER_PAGE), PAGE)
    assert browser.closed == [(1008, 'Forbidden')] and connect.calls == []


def test_the_bridge_refuses_a_terminal_token(monkeypatch, connect):
    authorized(monkeypatch, admin())
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1'), PAGE)
    assert browser.closed == [(1008, 'Forbidden')] and connect.calls == []


def test_the_bridge_refuses_a_page_id_that_is_not_a_page(monkeypatch, connect):
    authorized(monkeypatch, admin())
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1', scope='../json'), '../json')
    assert browser.closed == [(1008, 'Forbidden')] and connect.calls == []


def test_the_bridge_is_limited_in_number(monkeypatch, connect):
    authorized(monkeypatch, admin())
    monkeypatch.setattr(devtools, '_sessions', devtools.MAX_SESSIONS)
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1', scope=PAGE), PAGE)
    assert browser.closed == [(1013, 'Too many developer tools are open')] and connect.calls == []
    assert devtools._sessions == devtools.MAX_SESSIONS


@pytest.mark.parametrize('failure', [OSError('refused'), websockets.WebSocketException('bad handshake')])
def test_a_page_that_cannot_be_reached_ends_the_bridge_and_frees_the_slot(monkeypatch, connect, failure):
    authorized(monkeypatch, admin())
    connect.error = failure
    browser = FakeBrowser()
    run_bridge(browser, new_token('alice', 's1', scope=PAGE), PAGE)
    assert browser.closed == [(1000, '')]
    assert devtools._sessions == 0


def test_frames_pass_both_ways_and_the_socket_is_closed_at_the_end(monkeypatch, connect):
    authorized(monkeypatch, admin())
    browser = FakeBrowser(frames=['{"id":2}'])
    run_bridge(browser, new_token('alice', 's1', scope=PAGE), PAGE)
    assert connect.calls == [(f'ws://127.0.0.1:9223/devtools/page/{PAGE}', {'origin': None, 'max_size': None, 'ping_interval': None})]
    assert connect.page.sent == ['{"id":2}']
    assert browser.sent == ['{"id":1}']
    assert browser.closed == [(1000, '')]
    assert devtools._sessions == 0
    assert browser not in sessions._open


def test_the_protocol_of_the_bridge_needs_a_fresh_token_each_time(monkeypatch, connect):
    authorized(monkeypatch, admin())
    token = new_token('alice', 's1', scope=PAGE)
    run_bridge(FakeBrowser(), token, PAGE)
    second = FakeBrowser()
    run_bridge(second, token, PAGE)
    assert second.closed == [(1008, 'Forbidden')]
    assert len(connect.calls) == 1
