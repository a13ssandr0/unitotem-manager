"""The /vnc websocket and its bridge to the VNC server: the one-time token, the refusals before anything is started, the
bytes both ways, the end of a session (browser gone, idle, server dead, shutdown) and what is left behind after each.

test_vnc.py covers the command line, the server's lifecycle and the authorisation alone; this file drives the route.
"""
import asyncio
import os
import signal
import sys
import time
import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# routers.login builds the upload manager (needs /var/lib/unitotem): same stand-in as test_vnc.py.
_login = types.ModuleType('routers.login')
_login.LOGMAN = None
_login.NotAuthenticatedException = type('NotAuthenticatedException', (Exception,), {})
_login.require_perm = lambda perm: None
sys.modules.setdefault('routers.login', _login)

from unitotem_node import vnc as vnc_route  # noqa: E402
from unitotem_node import ws_token  # noqa: E402
from unitotem_node.system import vnc as vnc_system  # noqa: E402
from unitotem_node.system.screens.wayland import Output  # noqa: E402
from unitotem_node.system.vnc import IdleWatch, VncServer  # noqa: E402

# A stand-in for wayvnc: it listens on the socket it is given last, greets every client and echoes what it receives.
ECHO_SERVER = '''
import os, socket, sys, threading
path = sys.argv[-1]
s = socket.socket(socket.AF_UNIX)
s.bind(path)
os.chmod(path, 0o600)
s.listen(8)
def handle(c):
    c.sendall(b"RFB 003.008\\n")
    while True:
        data = c.recv(65536)
        if not data:
            break
        c.sendall(data)
while True:
    c, _ = s.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
'''

DIES = 'import sys\nprint("Can\'t open display :0", file=sys.stderr)\nsys.exit(1)\n'

ADMIN_SID = 'session-1'


def script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f'#!{sys.executable}\n{body}')
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def echo_binary(tmp_path):
    return script(tmp_path, 'fake-wayvnc', ECHO_SERVER)


@pytest.fixture
def root(tmp_path):
    return tmp_path / 'vnc'


@pytest.fixture
def servers(monkeypatch, root):
    """Every VncServer the route creates is built in tmp and recorded here."""
    created = []
    real = VncServer

    def factory(**kwargs):
        server = real(root=root, **kwargs)
        created.append(server)
        return server

    monkeypatch.setattr(vnc_route, 'VncServer', factory)
    monkeypatch.setattr(vnc_route, '_sessions', set())
    return created


@pytest.fixture
def admin(monkeypatch):
    """The account behind the websocket: an administrator, unless a test changes it."""
    state = SimpleNamespace(user=SimpleNamespace(name='bob', sid=ADMIN_SID, has_perm=SimpleNamespace(admin=True)))

    async def login(_request):
        return state.user

    monkeypatch.setattr(vnc_route, 'LOGMAN', login)
    return state


@pytest.fixture
def outputs(monkeypatch):
    shown = [Output('DP-1', True, True, 1920, 1200, 0, 0), Output('HDMI-1', True, False)]

    async def listed():
        return shown

    monkeypatch.setattr(vnc_route.wayland, 'outputs', listed)
    return shown


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(vnc_route.router)
    return TestClient(app)


@pytest.fixture
def binary(monkeypatch, echo_binary):
    monkeypatch.setattr(vnc_system, 'find_binary', lambda: echo_binary)
    return echo_binary


def token_for(name='bob', sid=ADMIN_SID):
    return ws_token.new_token(name, sid)


def wait_until(predicate, seconds=5):
    """Bounded wait for the session's own cleanup (it runs in the server's thread)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def process_gone(pid):
    """No such process, or a zombie (killed and not yet reaped by the loop that started it)."""
    try:
        with open(f'/proc/{pid}/stat') as stat:
            return stat.read().split(')')[-1].split()[0] == 'Z'
    except OSError:
        return True


def closed_with(ws):
    """The close code and reason the route ended the websocket with."""
    with pytest.raises(WebSocketDisconnect) as info:
        ws.receive_text()
    return info.value.code, info.value.reason


# ── the token ────────────────────────────────────────────────────────────────

def test_the_token_endpoint_gives_a_token_for_this_account_and_session():
    reply = asyncio.run(vnc_route.vnc_token(user=SimpleNamespace(name='bob', sid=ADMIN_SID)))
    token = reply['token']
    assert len(token) >= 32
    assert ws_token.take_token(token, 'bob', ADMIN_SID) is True
    assert ws_token.take_token(token, 'bob', ADMIN_SID) is False          # spent by the first use


def test_a_token_of_another_session_or_account_opens_nothing():
    token = ws_token.new_token('bob', ADMIN_SID)
    assert ws_token.take_token(token, 'bob', 'another-session') is False
    token = ws_token.new_token('bob', ADMIN_SID)
    assert ws_token.take_token(token, 'alice', ADMIN_SID) is False


def test_a_token_expires_after_its_lifetime(monkeypatch):
    token = ws_token.new_token('bob', ADMIN_SID)
    real = time.monotonic
    monkeypatch.setattr(ws_token.time, 'monotonic', lambda: real() + ws_token.TOKEN_TTL + 1)
    assert ws_token.take_token(token, 'bob', ADMIN_SID) is False


# ── refusals: nothing is started ─────────────────────────────────────────────

def test_a_websocket_from_another_origin_is_refused_before_a_token_is_read(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc', headers={'origin': 'https://evil.example'}) as ws:
        assert closed_with(ws) == (1008, 'Forbidden')
    assert servers == []


def test_a_websocket_without_a_token_is_refused_and_starts_nothing(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': 'not-a-token'})
        assert closed_with(ws) == (1008, 'Forbidden')
    assert servers == []


def test_a_token_of_another_account_is_refused(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for(name='alice')})
        assert closed_with(ws) == (1008, 'Forbidden')
    assert servers == []


def test_a_token_is_spent_by_its_first_use(client, admin, binary, servers, outputs):
    token = token_for()
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token})
        ws.send_bytes(b'ping')
        assert ws.receive_bytes().startswith(b'RFB 003.008')
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token})
        assert closed_with(ws) == (1008, 'Forbidden')


def test_without_the_server_binary_the_window_says_what_is_missing(client, admin, monkeypatch, servers, outputs):
    monkeypatch.setattr(vnc_system, 'find_binary', lambda: None)
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        assert closed_with(ws) == (1011, 'The VNC server is not installed (package wayvnc)')
    assert servers == []


def test_an_output_that_does_not_exist_is_refused_before_a_server_starts(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc?output=DP-9') as ws:
        ws.send_json({'token': token_for()})
        assert closed_with(ws) == (1011, 'Unknown output DP-9')
    assert servers == []


def test_an_output_showing_nothing_is_refused(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc?output=HDMI-1') as ws:
        ws.send_json({'token': token_for()})
        code, reason = closed_with(ws)
    assert code == 1011 and 'not showing anything' in reason
    assert servers == []


def test_a_third_screen_is_refused_while_two_are_watched(client, admin, binary, servers, outputs, monkeypatch):
    busy = {VncServer(binary='x'), VncServer(binary='x')}
    monkeypatch.setattr(vnc_route, '_sessions', busy)
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        assert closed_with(ws) == (1013, 'Too many screens are being watched')
    assert servers == []


# ── a session: the bytes, both ways ──────────────────────────────────────────

def test_bytes_go_both_ways_and_the_server_is_gone_when_the_browser_leaves(client, admin, binary, servers, outputs,
                                                                          root):
    with client.websocket_connect('/vnc?output=DP-1') as ws:
        ws.send_json({'token': token_for()})
        assert ws.receive_bytes().startswith(b'RFB 003.008')            # the server's greeting
        ws.send_bytes(b'\x00client-message')
        assert ws.receive_bytes() == b'\x00client-message'             # echoed back through the bridge
        assert len(servers) == 1 and servers[0].output == 'DP-1' and servers[0].control is False
        proc = servers[0].proc
    assert wait_until(lambda: process_gone(proc.pid))
    assert wait_until(lambda: not servers[0].directory.exists())
    assert not (root.exists() and any(root.iterdir()))
    assert vnc_route._sessions == set()


def test_control_is_granted_only_by_the_query_flag(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc?control=1') as ws:
        ws.send_json({'token': token_for()})
        ws.receive_bytes()
        assert servers[-1].control is True
        assert '--disable-input' not in servers[-1].command


def test_view_only_is_the_default_and_the_server_refuses_input(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc?control=0') as ws:
        ws.send_json({'token': token_for()})
        ws.receive_bytes()
        assert servers[-1].control is False
        assert '--disable-input' in servers[-1].command


def test_activity_frames_and_garbage_text_do_not_break_the_bridge(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        ws.receive_bytes()
        ws.send_text('{"activity": 1}')
        ws.send_text('not json at all')
        ws.send_text('[1, 2]')
        ws.send_bytes(b'still here')
        assert ws.receive_bytes() == b'still here'


def test_a_server_that_dies_at_once_tells_the_window_why(client, admin, monkeypatch, servers, outputs, tmp_path):
    monkeypatch.setattr(vnc_system, 'find_binary', lambda: script(tmp_path, 'dies', DIES))
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        code, reason = closed_with(ws)
    assert code == 1011
    assert reason.startswith('The VNC server could not attach to the screen')
    assert 'open display' in reason
    assert wait_until(lambda: not servers[0].directory.exists())
    assert vnc_route._sessions == set()


def test_a_server_that_ends_during_a_session_closes_the_window(client, admin, binary, servers, outputs):
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        ws.receive_bytes()
        os.kill(servers[0].proc.pid, signal.SIGKILL)
        code, reason = closed_with(ws)
    assert code == 1000 and reason == 'The VNC server ended'


# ── idle: the activity of the user keeps a session alive ─────────────────────

def test_an_idle_window_is_closed_and_activity_keeps_it_open(client, admin, binary, servers, outputs, monkeypatch):
    monkeypatch.setattr(vnc_route, 'IdleWatch', lambda: IdleWatch(timeout=0.6))
    with client.websocket_connect('/vnc') as ws:
        ws.send_json({'token': token_for()})
        ws.receive_bytes()
        for _ in range(8):                                  # 8 x 0.15 s of activity, longer than the timeout
            ws.send_text('{"activity": 1}')
            time.sleep(0.15)
        ws.send_bytes(b'alive')
        assert ws.receive_bytes() == b'alive'
        assert closed_with(ws) == (1000, 'Idle')            # then nothing: closed after the timeout


# ── shutdown ─────────────────────────────────────────────────────────────────

def test_stop_removes_the_directory_even_when_it_is_cancelled(tmp_path, echo_binary):
    """stop() promises the server is gone and its directory removed; a cancellation (the route's task is cancelled
    when the session ends) must not leave the directory behind."""
    server = VncServer(binary=echo_binary, root=tmp_path / 'vnc')

    async def scenario():
        await server.start()
        await server.connect()
        task = asyncio.create_task(server.stop())
        await asyncio.sleep(0)              # stop has sent SIGTERM and waits for the process
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert wait_until(lambda: process_gone(server.proc.pid))
    assert not server.directory.exists()


def test_stop_all_ends_every_live_session_and_its_directory(tmp_path, monkeypatch, echo_binary):
    root = tmp_path / 'vnc'
    live = [VncServer(binary=echo_binary, root=root) for _ in range(2)]

    async def scenario():
        for server in live:
            await server.start()
            await server.connect()
        monkeypatch.setattr(vnc_route, '_sessions', set(live))
        await vnc_route.stop_all()

    asyncio.run(scenario())
    assert vnc_route._sessions == set()
    for server in live:
        assert server.proc.returncode is not None
        assert not server.directory.exists()
    assert not any(root.iterdir())
    assert os.listdir(root) == []
