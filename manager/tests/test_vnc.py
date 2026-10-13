"""
The VNC window: the server's command line, the session token, the idle clock, the lifecycle of a server (with a
stand-in that only listens on the socket, so no X server is needed), cleaning up after a dead manager and the refusal of
everybody who is not an administrator.
"""
import asyncio
import os
import stat
import sys
import types
from types import SimpleNamespace

import pytest

# routers.login builds the upload manager, which creates /var/lib/unitotem: not something a test may do. The route only
# needs LOGMAN and the exception, so a stand-in is installed (every test replaces LOGMAN anyway).
_login = types.ModuleType('routers.login')
_login.LOGMAN = None
_login.NotAuthenticatedException = type('NotAuthenticatedException', (Exception,), {})
sys.modules.setdefault('routers.login', _login)

from unitotem_node import vnc as vnc_route  # noqa: E402
from unitotem_node.system import vnc  # noqa: E402
from unitotem_node.system.screens.xorg import XOutput  # noqa: E402
from unitotem_node.system.vnc import IdleWatch, VncError, VncServer, build_command, geometry  # noqa: E402


# ── command line ─────────────────────────────────────────────────────────────

def option(command, name):
    return command[command.index(name) + 1]


def test_command_never_listens_on_tcp_and_uses_the_unix_socket():
    command = build_command('/usr/bin/x0vncserver', '/run/unitotem-vnc/abc/rfb', control=False)
    assert option(command, '-rfbport') == '-1'
    assert option(command, '-rfbunixpath') == '/run/unitotem-vnc/abc/rfb'
    assert '-localhost' not in command and not any(c.startswith('-rfbauth') for c in command)


def test_command_is_view_only_by_default_and_server_enforced():
    command = build_command('x', '/s', control=False)
    assert '-AcceptKeyEvents=0' in command and '-AcceptPointerEvents=0' in command


def test_command_with_control_accepts_input():
    command = build_command('x', '/s', control=True)
    assert '-AcceptKeyEvents=1' in command and '-AcceptPointerEvents=1' in command


@pytest.mark.parametrize('control', [False, True])
def test_command_always_blocks_clipboard_and_resizing(control):
    command = build_command('x', '/s', control=control)
    for flag in ('-AcceptCutText=0', '-SendCutText=0', '-AcceptSetDesktopSize=0'):
        assert flag in command


def test_command_restricts_to_the_area_only_when_given():
    assert '-Geometry' not in build_command('x', '/s', False)
    assert option(build_command('x', '/s', False, '1920x1080+1920+0'), '-Geometry') == '1920x1080+1920+0'


def test_geometry_of_an_output_and_of_the_whole_desktop():
    assert geometry(None) is None
    assert geometry(XOutput('DP-1', True, True, 1080, 1920, 3840, 0, 'left')) == '1080x1920+3840+0'


def test_geometry_of_an_output_that_shows_nothing_is_refused():
    with pytest.raises(VncError):
        geometry(XOutput('HDMI-1', True, False))


def test_find_binary_prefers_the_alternatives_name_then_the_real_one():
    assert vnc.find_binary(which=lambda n: '/usr/bin/x0vncserver' if n == 'x0vncserver' else None) \
        == '/usr/bin/x0vncserver'
    assert vnc.find_binary(which=lambda n: None, exists=lambda p: p == '/usr/bin/X0tigervnc') == '/usr/bin/X0tigervnc'
    assert vnc.find_binary(which=lambda n: None, exists=lambda p: False) is None


# ── token ────────────────────────────────────────────────────────────────────

def test_tokens_are_long_unique_and_safe_in_a_path():
    tokens = {vnc.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 22 and '/' not in t and '.' not in t for t in tokens)


# ── idle clock ───────────────────────────────────────────────────────────────

def test_idle_watch_expires_after_the_timeout_and_activity_rearms_it():
    now = [100.0]
    watch = IdleWatch(timeout=60, clock=lambda: now[0])
    assert not watch.expired() and watch.remaining() == 60
    now[0] += 59
    assert not watch.expired()
    watch.touch()
    now[0] += 59
    assert not watch.expired()
    now[0] += 1
    assert watch.expired()


# ── lifecycle of a server ────────────────────────────────────────────────────

FAKE_SERVER = '''
import os, socket, sys
path = sys.argv[sys.argv.index("-rfbunixpath") + 1]
s = socket.socket(socket.AF_UNIX)
s.bind(path)
os.chmod(path, 0o600)
s.listen(1)
while True:
    c, _ = s.accept()
    c.sendall(b"RFB 003.008\\n")
'''


@pytest.fixture
def fake_binary(tmp_path):
    script = tmp_path / 'fake-vnc'
    script.write_text(f'#!{sys.executable}\n{FAKE_SERVER}')
    script.chmod(0o755)
    return str(script)


def test_a_server_starts_speaks_rfb_and_leaves_nothing_after_stop(tmp_path, fake_binary):
    async def scenario():
        server = VncServer(binary=fake_binary, root=tmp_path / 'vnc')
        await server.start()
        assert stat.S_IMODE(os.stat(tmp_path / 'vnc').st_mode) == 0o700
        assert stat.S_IMODE(os.stat(server.directory).st_mode) == 0o700
        reader, writer = await server.connect()
        assert (await reader.read(12)).startswith(b'RFB 003.')
        writer.close()
        pid = server.proc.pid
        await server.stop()
        assert server.proc.returncode is not None
        assert not server.directory.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(scenario())


def test_a_server_that_dies_at_once_reports_why(tmp_path):
    script = tmp_path / 'dies'
    script.write_text(f'#!{sys.executable}\nimport sys\nprint("Can\'t open display", file=sys.stderr)\nsys.exit(1)\n')
    script.chmod(0o755)

    async def scenario():
        server = VncServer(binary=str(script), root=tmp_path / 'vnc')
        await server.start()
        with pytest.raises(VncError, match="open display"):
            await server.connect(timeout=3)
        await server.stop()
        assert not server.directory.exists()

    asyncio.run(scenario())


def test_a_missing_binary_is_a_vnc_error_and_leaves_nothing(tmp_path):
    async def scenario():
        server = VncServer(binary=str(tmp_path / 'nope'), root=tmp_path / 'vnc')
        with pytest.raises(VncError):
            await server.start()
        assert not server.directory.exists()

    asyncio.run(scenario())


def test_sweep_kills_only_a_process_that_belongs_to_the_session(tmp_path):
    root = tmp_path / 'vnc'
    proc_root = tmp_path / 'proc'
    mine, other = root / 'mine', root / 'other'
    for d in (mine, other):
        d.mkdir(parents=True)
    (mine / 'pid').write_text('424242')
    (other / 'pid').write_text('434343')
    (proc_root / '424242').mkdir(parents=True)
    (proc_root / '424242' / 'cmdline').write_bytes(f'x0vncserver\0-rfbunixpath\0{mine}/rfb\0'.encode())
    (proc_root / '434343').mkdir(parents=True)
    (proc_root / '434343' / 'cmdline').write_bytes(b'something-else\0')
    killed = []
    real_kill = os.kill
    os.kill = lambda pid, sig: killed.append(pid)
    try:
        vnc.sweep_stale(root, proc_root)
    finally:
        os.kill = real_kill
    assert killed == [424242]
    assert not mine.exists() and not other.exists()


def test_servers_are_hashable_by_identity_for_the_set_of_live_sessions():
    a, b = VncServer(binary='x'), VncServer(binary='x')
    assert len({a, b}) == 2


# ── who gets in ──────────────────────────────────────────────────────────────

class FakeSocket:
    def __init__(self, headers=None):
        self.cookies = {}
        self.headers = headers or {}
        self.client = SimpleNamespace(host='10.0.0.9')
        self.sent = []

    async def accept(self):
        self.sent.append('accept')

    async def close(self, code, reason=''):
        self.sent.append(('close', code, reason))


def user(admin):
    return SimpleNamespace(name='bob', has_perm=SimpleNamespace(admin=admin))


def authorize(monkeypatch, who, headers=None):
    async def login(_request):
        if isinstance(who, Exception):
            raise who
        return who

    monkeypatch.setattr(vnc_route, 'LOGMAN', login)
    ws = FakeSocket(headers)
    return asyncio.run(vnc_route.authorize(ws)), ws


def test_an_administrator_gets_in(monkeypatch):
    who, ws = authorize(monkeypatch, user(True))
    assert who is not None and ws.sent == []


def test_a_user_without_admin_is_refused(monkeypatch):
    who, ws = authorize(monkeypatch, user(False))
    assert who is None and ws.sent == ['accept', ('close', 1008, 'Forbidden')]


def test_an_unauthenticated_client_is_refused(monkeypatch):
    who, ws = authorize(monkeypatch, vnc_route.NotAuthenticatedException())
    assert who is None and ws.sent == ['accept', ('close', 1008, 'Not Authenticated')]


def test_a_page_from_another_origin_is_refused_even_for_an_admin(monkeypatch):
    who, ws = authorize(monkeypatch, user(True), {'origin': 'https://evil.example', 'host': 'totem.local'})
    assert who is None and ws.sent[-1] == ('close', 1008, 'Forbidden')


def test_the_same_origin_is_accepted(monkeypatch):
    who, _ = authorize(monkeypatch, user(True), {'origin': 'https://totem.local', 'host': 'totem.local'})
    assert who is not None


def test_the_route_is_registered_and_the_outputs_call_needs_admin():
    assert any(r.path == '/vnc' for r in vnc_route.router.routes)
    from unitotem_node.api.vnc import Vnc
    assert 'admin' in Vnc.outputs.perms
