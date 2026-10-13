"""The operating-system account behind the web terminal: which one it is, and how its password is set."""
import asyncio
import stat
from collections import namedtuple

import pytest

from unitotem_node.system import os_account

Entry = namedtuple('Entry', 'pw_name pw_uid pw_shell')


def accounts(monkeypatch, *entries):
    monkeypatch.setattr(os_account.pwd, 'getpwall', lambda: list(entries))


def test_the_image_account_is_preferred(monkeypatch):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'), Entry('alice', 1000, '/bin/bash'),
             Entry('unitotem', 1001, '/bin/bash'))
    assert os_account.account() == 'unitotem'


def test_else_the_first_regular_user_who_can_log_in(monkeypatch):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'), Entry('daemon', 1, '/usr/sbin/nologin'),
             Entry('bob', 1002, '/bin/false'), Entry('carol', 1003, '/bin/bash'), Entry('alice', 1001, '/bin/sh'))
    assert os_account.account() == 'alice'


def test_the_kiosk_image_has_only_root(monkeypatch):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'), Entry('sync', 4, '/bin/sync'),
             Entry('dhcpcd', 100, '/bin/false'))
    assert os_account.account() == 'root'


def test_no_account_at_all(monkeypatch):
    accounts(monkeypatch, Entry('root', 0, '/usr/sbin/nologin'), Entry('daemon', 1, '/usr/sbin/nologin'))
    assert os_account.account() is None
    assert asyncio.run(os_account.set_password('a good long password')) is False


def test_the_password_goes_to_chpasswd_on_stdin_never_on_the_command_line(monkeypatch, tmp_path):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'))
    fake = tmp_path / 'chpasswd'
    seen = tmp_path / 'seen'
    fake.write_text(f'#!/bin/sh\necho "args:$#" > {seen}\ncat >> {seen}\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    assert asyncio.run(os_account.set_password('s3cret: with colon')) is True
    assert seen.read_text() == 'args:0\nroot:s3cret: with colon\n'


def test_a_failing_chpasswd_is_reported(monkeypatch, tmp_path):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'))
    fake = tmp_path / 'chpasswd'
    fake.write_text('#!/bin/sh\ncat >/dev/null\necho refused >&2\nexit 1\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')
    assert asyncio.run(os_account.set_password('a good long password')) is False


def test_a_newline_would_inject_a_second_account_and_is_refused(monkeypatch):
    accounts(monkeypatch, Entry('root', 0, '/bin/bash'))
    assert asyncio.run(os_account.set_password('x\nbob:evil')) is False
