"""
The terminal in the web UI: who may open it, the one-time token, the account the shell runs as (never an option, never
an account without a shell), the session limit, the pty between the shell and the websocket (size, input, output, idle
limit) and the ending of a session (hang-up, then terminate, then kill). No shell is started: the child process is a
stand-in, and every pty is a local pty that nothing runs in.
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
import fcntl
import os
import signal
import struct
import termios
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from plugins.unitotem_node import terminal
from plugins.unitotem_node.terminal import Terminal
from plugins.unitotem_node.ws_token import new_token, take_token
from utils import sessions


# ── stand-ins ────────────────────────────────────────────────────────────────

class FakeProc:
    """A child that ends when it is sent a signal, or never ends when `stuck` (every wait times out at once)."""

    def __init__(self, pid=4242, stuck=False):
        self.pid = pid
        self.returncode = None
        self.stuck = stuck
        self.signals = []
        self._done = asyncio.Event()

    def send_signal(self, sig):
        self.signals.append(sig)
        if not self.stuck:
            self.returncode = -sig
            self._done.set()

    async def wait(self):
        if self.returncode is None and self.stuck:
            raise asyncio.TimeoutError
        if self.returncode is None:
            await self._done.wait()
        return self.returncode


class FakeSocket:
    """The websocket of the terminal: a token in the first frame, then the given messages, then the end (or `gate`)."""

    def __init__(self, token_frame='', messages=(), origin=None, host='totem.local', gate=None, delay=0):
        self.cookies = {}
        self.headers = {'host': host}
        if origin is not None:
            self.headers['origin'] = origin
        self.client = SimpleNamespace(host='10.0.0.9')
        self.token_frame = token_frame
        self.messages = list(messages)
        self.gate = gate
        self.delay = delay
        self.entered = False
        self.sent = []
        self.sent_bytes = []
        self.closed = []

    async def accept(self):
        self.sent.append('accept')

    async def close(self, code, reason=''):
        self.closed.append((code, reason))

    async def send_bytes(self, data):
        self.sent_bytes.append(data)

    async def receive_text(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.token_frame

    async def receive(self):
        self.entered = True
        if self.messages:
            return self.messages.pop(0)
        if self.gate is not None:
            await self.gate.wait()
        return {'type': 'websocket.disconnect'}


def admin(name='alice', sid='s1', is_admin=True):
    return SimpleNamespace(name=name, sid=sid, has_perm=SimpleNamespace(admin=is_admin))


def login_as(monkeypatch, who):
    async def login(_request):
        if isinstance(who, Exception):
            raise who
        return who

    monkeypatch.setattr(terminal, 'LOGMAN', login)


@pytest.fixture
def spawned(monkeypatch):
    """Replaces the child: records each command and hands out a FakeProc (no process is started)."""
    calls = []
    procs = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        proc = FakeProc(pid=5000 + len(procs))
        procs.append(proc)
        return proc

    monkeypatch.setattr(terminal.asyncio, 'create_subprocess_exec', fake_exec)
    return SimpleNamespace(calls=calls, procs=procs)


@pytest.fixture(autouse=True)
def no_open_terminals(monkeypatch):
    monkeypatch.setattr(terminal, '_sessions', set())
    monkeypatch.setattr(terminal, 'has_shell', lambda name: True)


# ── who has a shell ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('shell, expected', [
    ('/bin/bash', True),
    ('/bin/sh', True),
    ('/usr/bin/zsh', True),
    ('/usr/sbin/nologin', False),
    ('/sbin/nologin', False),
    ('/bin/false', False),
    ('', False),
])
def test_an_account_has_a_shell_unless_its_login_shell_is_nologin_or_false(monkeypatch, shell, expected):
    monkeypatch.undo()     # the autouse fixture stubs has_shell; this test checks the real function
    monkeypatch.setattr(terminal.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_shell=shell))
    assert terminal.has_shell('bob') is expected


def test_an_unknown_account_has_no_shell(monkeypatch):
    monkeypatch.undo()
    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(terminal.pwd, 'getpwnam', missing)
    assert terminal.has_shell('ghost') is False


@pytest.mark.parametrize('name', ['alice', 'svc_web', 'a-b', '_x'])
def test_a_valid_account_name_is_accepted(name):
    assert terminal._checked_account(name) == name


@pytest.mark.parametrize('name', ['-f', '-', '', 'Root', 'a' * 33, 'a b', 'root\n', 'a;b', 'a/b', None, 5])
def test_anything_else_is_refused_as_the_account_of_the_shell(name):
    with pytest.raises(ValueError):
        terminal._checked_account(name)


# ── the token of the page ────────────────────────────────────────────────────

def test_a_token_is_issued_to_an_administrator_with_a_shell(monkeypatch):
    result = asyncio.run(terminal.terminal_token(admin('alice', 's1')))
    assert take_token(result['token'], 'alice', 's1') is True


def test_an_account_without_a_shell_gets_no_token(monkeypatch):
    monkeypatch.setattr(terminal, 'has_shell', lambda name: False)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(terminal.terminal_token(admin('alice', 's1')))
    assert refused.value.status_code == 409


# ── the window size ──────────────────────────────────────────────────────────

def test_the_window_size_is_sent_to_the_pty_and_never_zero(monkeypatch):
    sent = []
    monkeypatch.setattr(terminal.fcntl, 'ioctl', lambda fd, req, arg: sent.append((fd, req, struct.unpack('HHHH', arg))))
    terminal._set_size(5, 200, 50)
    terminal._set_size(5, 0, 0)
    assert sent[0] == (5, termios.TIOCSWINSZ, (50, 200, 0, 0))
    assert sent[1] == (5, termios.TIOCSWINSZ, (1, 1, 0, 0))


def test_a_terminal_resize_reaches_its_pty():
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        try:
            term.resize(100, 30)
            rows, cols, _, _ = struct.unpack('HHHH', fcntl.ioctl(term.master, termios.TIOCGWINSZ, bytes(8)))
            assert (rows, cols) == (30, 100)
        finally:
            await term.close()
    asyncio.run(go())


# ── the child: login for the account, nothing else ──────────────────────────

def test_the_shell_is_login_for_the_account_on_a_pty_it_controls(spawned):
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        await term.start()
        args, kwargs = spawned.calls[0]
        assert args == ('setsid', '-c', '/bin/login', '-f', 'alice')
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == term.slave
        assert kwargs['close_fds'] is True
        assert kwargs['env']['TERM'] == 'xterm-256color'
        assert set(kwargs['env']) == {'TERM', 'PATH'}          # no inherited environment
        assert 'sbin' in kwargs['env']['PATH']
        assert terminal._sessions == {term.proc.pid}
        await term.close()
        assert terminal._sessions == set()
        assert spawned.procs[0].signals == [signal.SIGHUP]
    asyncio.run(go())


def test_a_name_that_is_an_option_starts_nothing(spawned):
    async def go():
        term = Terminal(FakeSocket(), '-f')
        with pytest.raises(ValueError):
            await term.start()
        assert spawned.calls == []
        assert terminal._sessions == set()
        await term.close()
    asyncio.run(go())


def test_a_shell_that_cannot_be_started_raises_an_os_error(monkeypatch):
    async def missing(*args, **kwargs):
        raise FileNotFoundError('setsid')
    monkeypatch.setattr(terminal.asyncio, 'create_subprocess_exec', missing)

    async def go():
        term = Terminal(FakeSocket(), 'alice')
        with pytest.raises(OSError):
            await term.start()
        await term.close()
        assert terminal._sessions == set()
    asyncio.run(go())


# ── the pty between the shell and the websocket ─────────────────────────────

def test_what_the_shell_prints_is_queued_for_the_websocket():
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        try:
            os.write(term.slave, b'hello\n')
            term._on_readable()
            chunk = term.output.get_nowait()
            assert b'hello' in chunk
        finally:
            await term.close()
    asyncio.run(go())


def test_a_wake_up_with_nothing_to_read_queues_nothing_and_does_not_fail():
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        try:
            term._on_readable()
            assert term.output.empty()
        finally:
            await term.close()
    asyncio.run(go())


def test_keystrokes_reach_the_shell_and_count_as_activity():
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        try:
            term.last_input = 0.0
            term.write(b'x\n')
            assert term.last_input > 0
            assert os.read(term.slave, 100) == b'x\n'
        finally:
            await term.close()
    asyncio.run(go())


def test_the_output_is_sent_chunk_by_chunk_and_then_the_socket_closes():
    async def go():
        ws = FakeSocket()
        term = Terminal(ws, 'alice')
        term.output.put_nowait(b'a')
        term.output.put_nowait(b'b')
        term.output.put_nowait(None)
        await asyncio.wait_for(term.pump_output(), 5)
        assert ws.sent_bytes == [b'a', b'b']
        assert ws.closed == [(1000, 'Session ended')]
        await term.close()
    asyncio.run(go())


def test_the_window_ends_when_the_shell_ends(spawned):
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        await term.start()
        spawned.procs[0].send_signal(signal.SIGTERM)       # the shell exits
        await asyncio.wait_for(term.supervise(), 5)
        assert term.output.get_nowait() is None
        await term.close()
    asyncio.run(go())


def test_an_idle_terminal_is_closed(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(terminal, 'IDLE_TIMEOUT', 0.5)
    monkeypatch.setattr(terminal.asyncio, 'sleep', lambda seconds: real_sleep(0))

    async def go():
        ws = FakeSocket()
        term = Terminal(ws, 'alice')
        term.last_input = time.monotonic() - 1
        await asyncio.wait_for(term.watch_idle(), 5)
        assert ws.closed == [(1000, 'Idle')]
        await term.close()
    asyncio.run(go())


# ── ending a session ─────────────────────────────────────────────────────────

def test_a_shell_that_ignores_hang_up_is_terminated_then_killed(monkeypatch):
    proc = FakeProc(stuck=True)

    async def go():
        await terminal._terminate(proc)
    asyncio.run(go())
    assert proc.signals == [signal.SIGHUP, signal.SIGTERM, signal.SIGKILL]


def test_a_shell_that_is_gone_needs_no_signal():
    class Gone(FakeProc):
        def send_signal(self, sig):
            raise ProcessLookupError()

    asyncio.run(terminal._terminate(Gone()))


def test_closing_a_terminal_that_never_started_only_releases_the_pty():
    async def go():
        term = Terminal(FakeSocket(), 'alice')
        master, slave = term.master, term.slave
        await term.close()
        with pytest.raises(OSError):
            os.fstat(master)
        with pytest.raises(OSError):
            os.fstat(slave)
    asyncio.run(go())


# ── the websocket ────────────────────────────────────────────────────────────

def test_a_visitor_who_is_not_logged_in_is_refused(monkeypatch, spawned):
    login_as(monkeypatch, terminal.NotAuthenticatedException())
    ws = FakeSocket(new_token('alice', 's1'))
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1008, 'Not Authenticated')] and spawned.calls == []


def test_a_page_of_another_site_is_refused_even_for_an_administrator(monkeypatch, spawned):
    login_as(monkeypatch, admin())
    ws = FakeSocket(new_token('alice', 's1'), origin='https://evil.example')
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1008, 'Forbidden')] and spawned.calls == []


def test_a_user_without_admin_is_refused(monkeypatch, spawned):
    login_as(monkeypatch, admin(is_admin=False))
    ws = FakeSocket(new_token('alice', 's1'))
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1008, 'Forbidden')] and spawned.calls == []


def test_no_more_than_the_limit_of_terminals_is_open_at_once(monkeypatch, spawned):
    login_as(monkeypatch, admin())
    monkeypatch.setattr(terminal, '_sessions', set(range(terminal.MAX_SESSIONS)))
    ws = FakeSocket(new_token('alice', 's1'))
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1013, 'Too many open terminals')] and spawned.calls == []


@pytest.mark.parametrize('frame', ['', 'not json', '{"token": "made-up"}', '{"token": 7}'])
def test_a_websocket_without_a_valid_token_starts_nothing(monkeypatch, spawned, frame):
    login_as(monkeypatch, admin())
    ws = FakeSocket(frame)
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1008, 'Forbidden')] and spawned.calls == []


def test_a_token_of_another_account_or_session_starts_nothing(monkeypatch, spawned):
    login_as(monkeypatch, admin('alice', 's1'))
    import json
    for token in (new_token('bob', 's1'), new_token('alice', 's2')):
        ws = FakeSocket(json.dumps({'token': token}))
        asyncio.run(terminal.terminal(ws))
        assert ws.closed == [(1008, 'Forbidden')]
    assert spawned.calls == []


def test_a_token_is_good_for_one_terminal_only(monkeypatch, spawned):
    login_as(monkeypatch, admin())
    import json
    token = new_token('alice', 's1')
    first = FakeSocket(json.dumps({'token': token}))
    asyncio.run(terminal.terminal(first))
    second = FakeSocket(json.dumps({'token': token}))
    asyncio.run(terminal.terminal(second))
    assert second.closed == [(1008, 'Forbidden')]


def test_an_account_without_a_shell_gets_no_terminal(monkeypatch, spawned):
    import json
    login_as(monkeypatch, admin())
    monkeypatch.setattr(terminal, 'has_shell', lambda name: False)
    ws = FakeSocket(json.dumps({'token': new_token('alice', 's1')}))
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1008, 'This account has no shell')] and spawned.calls == []


def test_a_terminal_that_cannot_start_says_so(monkeypatch):
    import json
    login_as(monkeypatch, admin())

    async def missing(*args, **kwargs):
        raise FileNotFoundError('setsid')
    monkeypatch.setattr(terminal.asyncio, 'create_subprocess_exec', missing)
    ws = FakeSocket(json.dumps({'token': new_token('alice', 's1')}))
    asyncio.run(terminal.terminal(ws))
    assert ws.closed == [(1011, 'Cannot start the terminal')]
    assert terminal._sessions == set()


def test_an_open_terminal_takes_input_and_resizes_and_ends_with_its_socket(monkeypatch, spawned):
    import json
    login_as(monkeypatch, admin())
    done = []
    monkeypatch.setattr(Terminal, 'resize', lambda self, cols, rows: done.append(('resize', cols, rows)))
    monkeypatch.setattr(Terminal, 'write', lambda self, data: done.append(('write', data)))
    messages = [
        {'type': 'websocket.receive', 'text': json.dumps({'resize': [100, 40]})},
        {'type': 'websocket.receive', 'bytes': b'ls\n'},
        {'type': 'websocket.receive', 'text': 'not json'},            # ignored
        {'type': 'websocket.receive', 'text': json.dumps({'resize': [1]})},
        {'type': 'websocket.receive', 'text': json.dumps({'resize': ['a', 2]})},
        {'type': 'websocket.receive', 'text': json.dumps({'other': 1})},
    ]
    ws = FakeSocket(json.dumps({'token': new_token('alice', 's1')}), messages=messages)
    asyncio.run(terminal.terminal(ws))
    assert done == [('resize', 100, 40), ('write', b'ls\n')]
    assert spawned.procs[0].signals == [signal.SIGHUP]              # the shell is hung up when the window goes
    assert terminal._sessions == set()
    assert ws not in sessions._open


def test_a_pty_that_refuses_input_does_not_end_the_terminal(monkeypatch, spawned):
    import json
    login_as(monkeypatch, admin())

    def refuse(self, data):
        raise BlockingIOError()
    monkeypatch.setattr(Terminal, 'write', refuse)
    messages = [{'type': 'websocket.receive', 'bytes': b'x'}]
    ws = FakeSocket(json.dumps({'token': new_token('alice', 's1')}), messages=messages)
    asyncio.run(terminal.terminal(ws))
    assert terminal._sessions == set()


# ── the session limit under concurrent requests ─────────────────────────────

def test_concurrent_terminal_requests_cannot_exceed_the_session_limit(monkeypatch, spawned):
    """Two sockets pass the check together while their tokens are read; both must not start past MAX_SESSIONS."""
    import json
    login_as(monkeypatch, admin())
    monkeypatch.setattr(terminal, '_sessions', {1, 2, 3})          # one slot left
    gate = asyncio.Event()

    async def go():
        sockets = [FakeSocket(json.dumps({'token': new_token('alice', 's1')}), gate=gate, delay=0.05) for _ in range(2)]
        tasks = [asyncio.create_task(terminal.terminal(ws)) for ws in sockets]
        for _ in range(200):
            if all(ws.entered for ws in sockets):
                break
            await asyncio.sleep(0.01)
        count = len(terminal._sessions)
        gate.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        return count

    count = asyncio.run(go())
    assert count <= terminal.MAX_SESSIONS, f'{count} terminals open, the limit is {terminal.MAX_SESSIONS}'
