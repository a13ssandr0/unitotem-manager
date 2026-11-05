"""The first administrator is created once, under one lock; the first-access window closes with it."""
import os
import threading

import pytest

from utils import first_access, osusers


@pytest.fixture(autouse=True)
def lock_in_tmp(monkeypatch, tmp_path):
    """The real lock lives in /run/unitotem: every test uses a fresh directory instead, with a short wait."""
    monkeypatch.setattr(first_access, 'LOCK_PATH', str(tmp_path / 'run' / 'first-admin.lock'))
    monkeypatch.setattr(first_access, 'LOCK_TIMEOUT', 0.3)
    monkeypatch.setattr(first_access, 'LOCK_RETRY', 0.01)
    return tmp_path / 'run'


def acquire_in_other_thread():
    """Tries the lock from another thread (flock conflicts between open file descriptions, so it is a real test)."""
    outcome = []

    def attempt():
        try:
            with first_access.first_admin_lock():
                outcome.append('acquired')
        except first_access.FirstAccessBusy:
            outcome.append('busy')

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    return outcome[0]


def test_lock_is_refused_while_held_and_free_after(lock_in_tmp):
    with first_access.first_admin_lock():
        assert acquire_in_other_thread() == 'busy'
    assert acquire_in_other_thread() == 'acquired'


def test_lock_directory_and_file_modes(lock_in_tmp):
    assert not lock_in_tmp.exists()
    with first_access.first_admin_lock():
        assert os.stat(lock_in_tmp).st_mode & 0o777 == 0o700
        assert os.stat(first_access.LOCK_PATH).st_mode & 0o777 == 0o600


def test_lock_is_released_after_an_exception(lock_in_tmp):
    with pytest.raises(RuntimeError):
        with first_access.first_admin_lock():
            raise RuntimeError('setup failed')
    assert acquire_in_other_thread() == 'acquired'


@pytest.fixture
def osu(monkeypatch):
    """Records the osusers calls made by first_access, in order."""
    order = []
    state = {'admin_exists': False, 'first_open': True}
    monkeypatch.setattr(osusers, 'admin_exists', lambda: state['admin_exists'])
    monkeypatch.setattr(osusers, 'first_access_open', lambda: state['first_open'])
    monkeypatch.setattr(osusers, 'create_user', lambda *a, **k: order.append(('create_user', a, k)))
    monkeypatch.setattr(osusers, 'close_first_access', lambda: order.append(('close',)))
    return type('Osu', (), {'order': order, 'state': state})


def test_create_first_admin_creates_an_administrator_then_closes_the_window(osu):
    first_access.create_first_admin('admin', 'pw-1234567890')
    assert osu.order == [('create_user', ('admin', 'pw-1234567890'), {'admin': True}), ('close',)]


def test_create_first_admin_is_refused_when_an_administrator_exists(osu):
    osu.state['admin_exists'] = True
    with pytest.raises(first_access.FirstAccessDone):
        first_access.create_first_admin('admin', 'pw-1234567890')
    assert osu.order == []


def test_create_first_admin_checks_again_under_the_lock(osu, monkeypatch):
    """A second setup that was waiting for the lock sees the administrator the first one made."""
    seen = []

    def admin_exists():
        seen.append(acquire_in_other_thread())   # the lock is held by create_first_admin right now
        return True

    monkeypatch.setattr(osusers, 'admin_exists', admin_exists)
    with pytest.raises(first_access.FirstAccessDone):
        first_access.create_first_admin('admin', 'pw-1234567890')
    assert seen == ['busy']
    assert osu.order == []


def test_create_first_admin_failure_keeps_the_window_open(osu, monkeypatch):
    def refuse(*a, **k):
        raise osusers.OSUserError('adduser failed')

    monkeypatch.setattr(osusers, 'create_user', refuse)
    with pytest.raises(osusers.OSUserError):
        first_access.create_first_admin('admin', 'pw-1234567890')
    assert ('close',) not in osu.order
    assert acquire_in_other_thread() == 'acquired'      # released


@pytest.mark.parametrize('admin_exists, first_open, closes', [
    (True, True, True),       # an administrator exists, the window is still open: close it
    (True, False, False),     # already closed
    (False, True, False),     # no administrator yet: the window stays open
    (False, False, False),
])
def test_reconcile_closes_only_when_an_administrator_exists_and_the_window_is_open(osu, admin_exists, first_open,
                                                                                    closes):
    osu.state['admin_exists'] = admin_exists
    osu.state['first_open'] = first_open
    first_access.reconcile()
    assert osu.order == ([('close',)] if closes else [])
