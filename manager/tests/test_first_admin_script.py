"""
The first-access script (debian/unitotem-first-admin): the flow, the attempt limit, the environment and process
isolation, the signal handling, and one end-to-end run under a pseudo-terminal on a copy with stub modules.

The script is loaded from its file (no .py extension). Every system call is mocked: osusers functions, syslog,
the lock file (tmp_path), and any process spawn fails the test.
"""
import importlib.machinery
import importlib.util
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import syslog
import termios
import time
from pathlib import Path

import pytest
from loguru import logger

from utils import first_access, osusers, passwords

REPO_MANAGER = Path(__file__).resolve().parents[1]          # manager/
SCRIPT = Path(__file__).resolve().parents[2] / 'debian' / 'unitotem-first-admin'

_loader = importlib.machinery.SourceFileLoader('unitotem_first_admin_under_test', str(SCRIPT))
script = importlib.util.module_from_spec(importlib.util.spec_from_loader(_loader.name, _loader))
_loader.exec_module(script)

ARGV = ['-c', 'bash -c id']
CLEAN_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
HOSTILE_ENV = {
    'SHELL': '/bin/sh', 'PATH': '/tmp/evil:/usr/bin', 'PAGER': '/tmp/evil/pager', 'EDITOR': '/tmp/evil/ed',
    'LD_PRELOAD': '/tmp/evil/hook.so', 'PYTHONPATH': '/tmp/evil', 'BASH_ENV': '/tmp/evil/rc', 'IFS': ' ',
    'LANG': 'en_US.UTF-8',
}
MSG_ADMIN_EXISTS = 'Root accepts SSH keys only. Log in with an administrator account.'
MSG_NO_TERMINAL = 'First access: connect with an interactive terminal (ssh -t).'
MSG_FAILED = 'First access failed. Details are in the system log.'
GOOD_PASSWORD = 'Correct-Horse-42'
RULE_LINES = ['  - ' + rule['text'] for rule in passwords.rules_spec()['rules']]
RULES_OUT = 'Password rules:\n' + ''.join(line + '\n' for line in RULE_LINES)
SIGNALS = (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP, signal.SIGHUP, signal.SIGTERM)


class Exhausted(BaseException):
    """Raised by scripted prompts when the test gave no more answers (BaseException: the script never swallows it)."""


class FakeOS:
    def __init__(self):
        self.admin = False
        self.calls = []
        self.umask_at_create = None


def _scripted(values):
    """A prompt function answering from `values`; an exception instance in the list is raised instead."""
    remaining = list(values)
    prompts = []

    def ask(prompt=''):
        prompts.append(prompt)
        if not remaining:
            raise Exhausted(prompt)
        value = remaining.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value
    ask.prompts = prompts
    return ask


def _forbidden_spawn(monkeypatch):
    spawned = []

    def forbidden(*args, **kwargs):
        spawned.append(args)
        raise AssertionError('a process was spawned')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    names = [n for n in dir(os) if n.startswith(('exec', 'spawn', 'posix_spawn'))]
    names += ['system', 'popen', 'fork', 'forkpty']
    for name in names:
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    return spawned


def _run(monkeypatch, tmp_path, *, input_fn, getpass_fn=None, is_tty=True, argv=ARGV):
    """Runs script.main with every spawn guarded and checks the clean environment afterwards."""
    spawned = _forbidden_spawn(monkeypatch)
    rc = script.main(argv, input_fn=input_fn, getpass_fn=getpass_fn or _scripted([]),
                     manager_dir=str(REPO_MANAGER), is_tty=lambda: is_tty)
    assert spawned == []
    assert dict(os.environ) == CLEAN_ENV
    return rc


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    saved_env = dict(os.environ)
    saved_umask = os.umask(0)
    os.umask(saved_umask)
    saved_path = list(sys.path)
    os.environ.clear()
    os.environ.update(HOSTILE_ENV)
    monkeypatch.setattr(first_access, 'LOCK_PATH', str(tmp_path / 'lock' / 'first-admin.lock'))
    yield
    os.environ.clear()
    os.environ.update(saved_env)
    os.umask(saved_umask)
    sys.path[:] = saved_path
    # the script reconfigures the global loguru logger; send it back to the default stderr sink
    logger.remove()
    logger.add(sys.__stderr__)


@pytest.fixture
def fake_os(monkeypatch):
    fake = FakeOS()

    def admin_exists():
        return fake.admin

    def create_user(name, password, hashed=False, admin=False):
        old = os.umask(0)
        os.umask(old)
        fake.umask_at_create = old
        fake.calls.append(('create', name, password, admin, hashed))
        if admin:
            fake.admin = True

    def close_first_access():
        fake.calls.append(('close',))

    def real_tool(*args, **kwargs):
        raise AssertionError('a system account tool was called')

    monkeypatch.setattr(osusers, 'admin_exists', admin_exists)
    monkeypatch.setattr(osusers, 'create_user', create_user)
    monkeypatch.setattr(osusers, 'close_first_access', close_first_access)
    monkeypatch.setattr(osusers, 'first_access_open', lambda: True)
    monkeypatch.setattr(osusers, '_run', real_tool)
    return fake


@pytest.fixture
def syslog_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(syslog, 'syslog', lambda *args: calls.append(args[-1] if len(args) == 1 else args[-1]))
    monkeypatch.setattr(syslog, 'openlog', lambda *args, **kwargs: calls.append(('openlog',) + args))
    return calls


# Already set up, terminal, prompts

def test_already_set_up_prints_message_and_exits_0_without_prompting(monkeypatch, tmp_path, fake_os, capfd):
    fake_os.admin = True
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted([]), getpass_fn=_scripted([]))
    assert rc == 0
    assert capfd.readouterr().out == MSG_ADMIN_EXISTS + '\n'
    assert fake_os.calls == []


@pytest.mark.parametrize('admin', [False, True])
def test_non_tty_exits_1_without_prompting(monkeypatch, tmp_path, fake_os, capfd, admin):
    fake_os.admin = admin
    prompts = _scripted([])
    rc = _run(monkeypatch, tmp_path, input_fn=prompts, getpass_fn=_scripted([]), is_tty=False)
    assert rc == 1
    assert prompts.prompts == []
    expected = MSG_ADMIN_EXISTS if admin else MSG_NO_TERMINAL
    assert capfd.readouterr().out == expected + '\n'
    assert fake_os.calls == []


def test_success_creates_admin_then_closes_window_with_umask_077(monkeypatch, tmp_path, fake_os, capfd):
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']),
              getpass_fn=_scripted([GOOD_PASSWORD, GOOD_PASSWORD]))
    assert rc == 0
    assert capfd.readouterr().out == RULES_OUT + 'Administrator alice created. Log in again as alice.\n'
    assert fake_os.calls == [('create', 'alice', GOOD_PASSWORD, True, False), ('close',)]
    assert fake_os.umask_at_create == 0o077


def test_real_create_first_admin_reports_done_when_admin_appears_under_lock(monkeypatch, tmp_path, fake_os, capfd):
    # The script checks once (no admin), create_first_admin re-checks under the lock (admin now exists)
    answers = iter([False, True])
    monkeypatch.setattr(osusers, 'admin_exists', lambda: next(answers))
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']),
              getpass_fn=_scripted([GOOD_PASSWORD, GOOD_PASSWORD]))
    assert rc == 0
    assert capfd.readouterr().out == RULES_OUT + MSG_ADMIN_EXISTS + '\n'
    assert fake_os.calls == []
    assert (tmp_path / 'lock' / 'first-admin.lock').exists()


# Attempts and input validation

def test_mismatching_passwords_count_as_attempts(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['one-one-111', 'two-two-222'] * 3)
    prompts = _scripted(['alice'] * 3)
    rc = _run(monkeypatch, tmp_path, input_fn=prompts, getpass_fn=getpass)
    assert rc == 1
    assert len(prompts.prompts) == 3
    out = capfd.readouterr().out
    assert out.count('The passwords do not match.') == 3
    assert out.endswith('Too many attempts.\n')
    assert fake_os.calls == []


def test_success_after_a_mismatch_uses_a_later_attempt(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['a-first-1', 'b-second-2', GOOD_PASSWORD, GOOD_PASSWORD])
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice', 'alice']), getpass_fn=getpass)
    assert rc == 0
    assert fake_os.calls[0] == ('create', 'alice', GOOD_PASSWORD, True, False)


@pytest.mark.parametrize('name', ['Alice', 'root', '', '-x', '1abc', 'a' * 33, 'bad name', 'a/b'])
def test_invalid_name_is_rejected_without_reading_password(monkeypatch, tmp_path, fake_os, capfd, name):
    getpass = _scripted([])
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted([name] * 3), getpass_fn=getpass)
    assert rc == 1
    assert getpass.prompts == []
    assert fake_os.calls == []
    assert capfd.readouterr().out.endswith('Too many attempts.\n')


def test_root_is_reserved_with_its_own_message(monkeypatch, tmp_path, fake_os, capfd):
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['root'] * 3), getpass_fn=_scripted([]))
    assert rc == 1
    assert capfd.readouterr().out.count('The username root is reserved') == 3


def test_exactly_three_attempts_then_too_many_attempts(monkeypatch, tmp_path, fake_os, capfd):
    prompts = _scripted(['Alice'] * 10)
    rc = _run(monkeypatch, tmp_path, input_fn=prompts, getpass_fn=_scripted([]))
    assert rc == 1
    assert len(prompts.prompts) == 3
    assert capfd.readouterr().out.endswith('Too many attempts.\n')
    assert fake_os.calls == []


# Password policy



def test_weak_password_is_refused_with_reasons_then_strong_accepted(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['short1', 'short1', GOOD_PASSWORD, GOOD_PASSWORD])
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice', 'alice']), getpass_fn=getpass)
    assert rc == 0
    out = capfd.readouterr().out
    for reason in passwords.validate_password('short1', 'alice'):
        assert reason + '\n' in out
    assert fake_os.calls[0] == ('create', 'alice', GOOD_PASSWORD, True, False)


def test_rules_are_printed_once_before_the_first_password_prompt(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['short1', 'short1', 'short2', 'short2', GOOD_PASSWORD, GOOD_PASSWORD])
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice'] * 3), getpass_fn=getpass)
    assert rc == 0
    out = capfd.readouterr().out
    lines = out.splitlines()
    assert lines.count('Password rules:') == 1
    assert lines[:len(RULE_LINES) + 1] == ['Password rules:'] + RULE_LINES
    assert out.index('Password rules:') < out.index(passwords.validate_password('short1', 'alice')[0])


def test_password_containing_the_username_is_refused(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['my-ALICE-pass-42', 'my-ALICE-pass-42', GOOD_PASSWORD, GOOD_PASSWORD])
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice', 'alice']), getpass_fn=getpass)
    assert rc == 0
    out = capfd.readouterr().out
    assert passwords.validate_password('my-ALICE-pass-42', 'alice')
    assert passwords.validate_password('my-ALICE-pass-42', 'alice')[0] in out


def test_three_refused_passwords_exit_1_with_too_many_attempts(monkeypatch, tmp_path, fake_os, capfd):
    getpass = _scripted(['short1', 'short1'] * 3)
    prompts = _scripted(['alice'] * 3)
    rc = _run(monkeypatch, tmp_path, input_fn=prompts, getpass_fn=getpass)
    assert rc == 1
    assert len(prompts.prompts) == 3
    out = capfd.readouterr().out
    assert out.count('Password rules:') == 1
    assert out.count(passwords.validate_password('short1', 'alice')[0] + '\n') == 3
    assert out.endswith('Too many attempts.\n')
    assert fake_os.calls == []


# Interruption and failure

@pytest.mark.parametrize('interrupt', [EOFError(), KeyboardInterrupt()])
def test_eof_or_interrupt_exits_1_quietly(monkeypatch, tmp_path, fake_os, capfd, interrupt):
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted([interrupt]), getpass_fn=_scripted([]))
    assert rc == 1
    captured = capfd.readouterr()
    assert captured.out == RULES_OUT
    assert captured.err == ''
    assert fake_os.calls == []


def test_eof_at_password_prompt_exits_1_quietly(monkeypatch, tmp_path, fake_os, capfd):
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']), getpass_fn=_scripted([EOFError()]))
    assert rc == 1
    assert capfd.readouterr().out == RULES_OUT
    assert fake_os.calls == []


def test_generic_failure_prints_only_the_short_message_and_logs_details(monkeypatch, tmp_path, fake_os, capfd,
                                                                         syslog_calls):
    def broken(name, password):
        raise RuntimeError('useradd failed for the account')

    monkeypatch.setattr(first_access, 'create_first_admin', broken)
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']),
              getpass_fn=_scripted([GOOD_PASSWORD, GOOD_PASSWORD]))
    assert rc == 1
    captured = capfd.readouterr()
    assert captured.out == RULES_OUT + MSG_FAILED + '\n'
    assert captured.err == ''
    logged = '\n'.join(str(item) for item in syslog_calls)
    assert 'Traceback' in logged
    assert 'RuntimeError: useradd failed for the account' in logged
    assert GOOD_PASSWORD not in captured.out + captured.err + logged


def test_generic_failure_restores_signal_handlers(monkeypatch, tmp_path, fake_os, capfd, syslog_calls,
                                                  sentinel_signals):
    def broken(name, password):
        raise RuntimeError('boom')

    monkeypatch.setattr(first_access, 'create_first_admin', broken)
    _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']), getpass_fn=_scripted([GOOD_PASSWORD] * 2))
    for signum in SIGNALS:
        assert signal.getsignal(signum) is _sentinel_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_osuser_error_is_printed_and_the_prompt_repeats(monkeypatch, tmp_path, fake_os, capfd):
    outcomes = iter([osusers.OSUserError('useradd: name taken'), None])

    def create(name, password, admin=False):
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome
        fake_os.calls.append(('create', name, password, admin, False))
        fake_os.admin = True

    monkeypatch.setattr(first_access, 'create_first_admin', create)
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice', 'alice']),
              getpass_fn=_scripted([GOOD_PASSWORD] * 4))
    assert rc == 0
    assert 'useradd: name taken' in capfd.readouterr().out


def test_first_admin_done_during_create_reports_already_set_up(monkeypatch, tmp_path, fake_os, capfd):
    def done(name, password):
        raise first_access.FirstAccessDone()

    monkeypatch.setattr(first_access, 'create_first_admin', done)
    prompts = _scripted(['alice', 'bob'])
    rc = _run(monkeypatch, tmp_path, input_fn=prompts, getpass_fn=_scripted([GOOD_PASSWORD] * 4))
    assert rc == 0
    assert capfd.readouterr().out == RULES_OUT + MSG_ADMIN_EXISTS + '\n'
    assert len(prompts.prompts) == 1


def test_first_access_busy_exits_1(monkeypatch, tmp_path, fake_os, capfd):
    def busy(name, password):
        raise first_access.FirstAccessBusy()

    monkeypatch.setattr(first_access, 'create_first_admin', busy)
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted(['alice']), getpass_fn=_scripted([GOOD_PASSWORD] * 2))
    assert rc == 1
    assert capfd.readouterr().out == RULES_OUT + 'Another first access is in progress. Try again in a minute.\n'


# Environment and argv

@pytest.mark.parametrize('argv', [['-c', 'bash'], ['-c', 'sh -i'], ['sftp-server'], ['-s', 'sftp'], []])
def test_argv_is_ignored_and_environment_replaced(monkeypatch, tmp_path, fake_os, capfd, argv):
    monkeypatch.setattr(sys, 'argv', ['unitotem-first-admin', '-c', 'bash'])
    fake_os.admin = True
    rc = _run(monkeypatch, tmp_path, input_fn=_scripted([]), getpass_fn=_scripted([]), argv=argv)
    assert rc == 0
    assert capfd.readouterr().out == MSG_ADMIN_EXISTS + '\n'
    assert sys.argv == ['unitotem-first-admin', '-c', 'bash']


def test_umask_is_077_only_inside_the_run(monkeypatch, tmp_path, fake_os, capfd):
    before = os.umask(0)
    os.umask(before)
    fake_os.admin = True
    _run(monkeypatch, tmp_path, input_fn=_scripted([]), getpass_fn=_scripted([]))
    assert os.umask(before) == 0o077
    os.umask(before)


# Signals and timeout

_sentinel_handler_calls = []


def _sentinel_handler(signum, frame):
    _sentinel_handler_calls.append(signum)


@pytest.fixture
def sentinel_signals():
    saved = {signum: signal.getsignal(signum) for signum in SIGNALS + (signal.SIGALRM,)}
    for signum in saved:
        signal.signal(signum, _sentinel_handler)
    yield
    for signum, handler in saved.items():
        signal.signal(signum, handler if handler is not None else signal.SIG_DFL)


def test_interrupt_signals_raise_system_exit_while_running(monkeypatch, tmp_path, fake_os, capfd,
                                                           sentinel_signals):
    installed, raised = [], []

    def ask(prompt=''):
        for signum in SIGNALS:
            handler = signal.getsignal(signum)
            if handler is not _sentinel_handler:
                installed.append(signum)
            try:
                handler(signum, None)
            except SystemExit:
                raised.append(signum)
        assert 290 < signal.getitimer(signal.ITIMER_REAL)[0] <= 300
        raise EOFError()

    rc = _run(monkeypatch, tmp_path, input_fn=ask, getpass_fn=_scripted([]))
    assert rc == 1
    assert installed == list(SIGNALS)
    assert raised == list(SIGNALS)
    for signum in SIGNALS:
        assert signal.getsignal(signum) is _sentinel_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_alarm_handler_prints_timeout_and_exits(monkeypatch, tmp_path, fake_os, capfd, sentinel_signals):
    exited = []

    def ask(prompt=''):
        handler = signal.getsignal(signal.SIGALRM)
        assert handler is not _sentinel_handler
        try:
            handler(signal.SIGALRM, None)
        except SystemExit:
            exited.append(True)
        raise EOFError()

    rc = _run(monkeypatch, tmp_path, input_fn=ask, getpass_fn=_scripted([]))
    assert rc == 1
    assert exited == [True]
    assert capfd.readouterr().out == RULES_OUT + 'Timed out.\n'
    assert signal.getsignal(signal.SIGALRM) is _sentinel_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


# End to end under a pseudo-terminal, on a copy with stub modules (no system access)

STUB_LOGURU = 'class _Logger:\n    def remove(self, *args):\n        pass\n\n    def add(self, *args, **kwargs):\n        pass\n\n\nlogger = _Logger()\n'
STUB_OSUSERS = '''import re


class OSUserError(Exception):
    pass


def valid_name(name):
    return bool(re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', name))


def admin_exists():
    return ADMIN_EXISTS
'''
STUB_FIRST_ACCESS = '''class FirstAccessDone(Exception):
    pass


class FirstAccessBusy(Exception):
    pass


def create_first_admin(name, password):
    open(MARKER, 'w').close()
    raise AssertionError('create_first_admin must not run in this test')
'''


def _build_copy(tmp_path, admin_exists):
    stub = tmp_path / 'stub'
    (stub / 'utils').mkdir(parents=True)
    (stub / 'loguru').mkdir()
    (stub / 'loguru' / '__init__.py').write_text(STUB_LOGURU)
    (stub / 'utils' / '__init__.py').write_text('')
    shutil.copy(REPO_MANAGER / 'utils' / 'passwords.py', stub / 'utils' / 'passwords.py')
    marker = tmp_path / 'account-created'
    (stub / 'utils' / 'osusers.py').write_text(
        STUB_OSUSERS.replace('ADMIN_EXISTS', 'True' if admin_exists else 'False'))
    (stub / 'utils' / 'first_access.py').write_text(STUB_FIRST_ACCESS.replace('MARKER', repr(str(marker))))
    text = SCRIPT.read_text()
    text = text.replace("MANAGER_DIR = '/usr/share/unitotem-manager'", f'MANAGER_DIR = {str(stub)!r}')
    assert str(stub) in text
    copy = tmp_path / 'unitotem-first-admin-copy.py'
    copy.write_text(text)
    return copy, marker


def _pty_child():
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _run_on_pty(copy, send=None, timeout=15.0):
    master, slave = pty.openpty()
    proc = subprocess.Popen([sys.executable, '-I', str(copy), '-c', 'bash -c id'],
                            stdin=slave, stdout=slave, stderr=slave, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                            close_fds=True, preexec_fn=_pty_child)
    os.close(slave)
    out, sent = b'', False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                if send and not sent and send[0] in out:
                    os.write(master, send[1])
                    sent = True
            elif proc.poll() is not None:
                break
        else:
            proc.kill()
            proc.wait()
            pytest.fail('script did not exit: ' + out.decode(errors='replace'))
        rc = proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(master)
    return rc, out.decode(errors='replace')


def test_e2e_admin_exists_prints_message_and_exits_0_on_pty(tmp_path):
    copy, marker = _build_copy(tmp_path, admin_exists=True)
    rc, out = _run_on_pty(copy)
    assert rc == 0
    assert MSG_ADMIN_EXISTS in out
    assert 'uid=' not in out
    assert not marker.exists()


def test_e2e_ctrl_c_at_first_prompt_exits_promptly_without_account(tmp_path):
    copy, marker = _build_copy(tmp_path, admin_exists=False)
    started = time.monotonic()
    rc, out = _run_on_pty(copy, send=(b'User name: ', b'\x03'))
    elapsed = time.monotonic() - started
    assert rc == 1
    assert elapsed < 10
    assert 'Traceback' not in out
    assert 'uid=' not in out
    assert not marker.exists()
