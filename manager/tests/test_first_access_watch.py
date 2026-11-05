"""The first-access watch: checked at start-up, then again when /etc/group or /etc/gshadow changes (debounced). A pipe
carries the inotify events, and a fake loop captures the reader and the timers."""
import os
import struct
from types import SimpleNamespace

import pytest

from utils import first_access
from utils import first_access_watch as watch

EVENT = struct.Struct('iIII')


class FakeHandle:
    def __init__(self, when, callback):
        self.when = when
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeLoop:
    def __init__(self):
        self.reader = None
        self.timers = []

    def add_reader(self, fd, callback):
        self.reader = (fd, callback)

    def remove_reader(self, fd):
        self.reader = None

    def call_later(self, delay, callback):
        handle = FakeHandle(delay, callback)
        self.timers.append(handle)
        return handle

    def live_timers(self):
        return [t for t in self.timers if not t.cancelled]


def event(name: bytes):
    padded = name + b'\0' * (-len(name) % 8 or 8)
    return EVENT.pack(1, watch.IN_CLOSE_WRITE, 0, len(padded)) + padded


@pytest.fixture
def world(monkeypatch):
    """A pipe stands in for the inotify descriptor; has_admin, reconcile and the web UI are counted fakes."""
    r, w = os.pipe()
    os.set_blocking(r, False)
    state = SimpleNamespace(admin=False, reconciles=0, refreshes=0, loop=FakeLoop(), read_fd=r, write_fd=w)
    monkeypatch.setattr(watch, '_inotify', lambda path: r)
    monkeypatch.setattr(watch, 'user_manager', SimpleNamespace(has_admin=lambda: state.admin))

    def reconcile():
        state.reconciles += 1
    monkeypatch.setattr(first_access, 'reconcile', reconcile)
    monkeypatch.setattr(watch, 'WebviewManager', SimpleNamespace(_instance=SimpleNamespace(
        refresh_idle=lambda: setattr(state, 'refreshes', state.refreshes + 1))))
    yield state
    os.close(r)
    os.close(w)


def test_start_checks_once_and_watches(world):
    watch.start(world.loop)
    assert world.reconciles == 1
    assert world.loop.reader is not None and world.loop.reader[0] == world.read_fd


def test_without_inotify_it_logs_and_returns_after_one_check(world, monkeypatch):
    monkeypatch.setattr(watch, '_inotify', lambda path: None)
    watch.start(world.loop)
    assert world.reconciles == 1
    assert world.loop.reader is None and world.loop.timers == []


def test_inotify_setup_fails_on_a_missing_path():
    assert watch._inotify('/nonexistent/unitotem-test-path') is None


def test_an_administrator_added_from_outside_closes_the_window_once(world):
    watch.start(world.loop)
    os.write(world.write_fd, event(b'group'))
    world.loop.reader[1]()                       # the loop reports the descriptor readable
    timers = world.loop.live_timers()
    assert len(timers) == 1 and timers[0].when == watch.DEBOUNCE

    world.admin = True
    timers[0].callback()                         # the debounce delay has passed
    assert world.reconciles == 2                 # start-up, then the change
    assert world.refreshes == 1


def test_no_change_of_the_administrators_does_nothing(world):
    watch.start(world.loop)
    os.write(world.write_fd, event(b'gshadow'))
    world.loop.reader[1]()
    world.loop.live_timers()[0].callback()
    assert world.reconciles == 1 and world.refreshes == 0     # only the start-up check ran


def test_bursts_are_debounced_to_one_check(world):
    watch.start(world.loop)
    for _ in range(3):
        os.write(world.write_fd, event(b'group'))
        world.loop.reader[1]()
    timers = world.loop.live_timers()
    assert len(timers) == 1
    assert [t.cancelled for t in world.loop.timers] == [True, True, False]


def test_other_files_do_not_schedule_a_check(world):
    watch.start(world.loop)
    os.write(world.write_fd, event(b'passwd'))
    world.loop.reader[1]()
    assert world.loop.timers == []


def test_a_removed_administrator_is_checked_too(world):
    world.admin = True
    watch.start(world.loop)
    os.write(world.write_fd, event(b'group'))
    world.loop.reader[1]()
    world.admin = False
    world.loop.live_timers()[0].callback()
    assert world.reconciles == 1                 # no administrator: nothing to close
    assert world.refreshes == 0
