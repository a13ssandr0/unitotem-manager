"""Scheduled enable and disable of assets fire at their due second, on a fake clock.

utils/async_timer.Timer computes its delay from datetime.now() and sleeps on the event loop. Both are replaced here by a
virtual clock: a test advances time by exact seconds, and what is due at that moment (and only that) runs. The schedule is
also exercised through the Scheduler API (edit and add), and through the copy and move of an asset between playlists.
"""
import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from utils import async_timer
from utils.models import assets as assets_model
from utils.models.assets import AssetsManager
from utils.models.command_line import cmdargs

REAL_DATETIME = datetime


class FakeClock:
    def __init__(self, loop):
        self.loop = loop
        self.wall = REAL_DATETIME.now().replace(microsecond=0)     # what datetime.now() returns
        self.mono = 1000.0                                          # what the loop's sleeps count
        self.waiters: list[tuple[float, asyncio.Future]] = []

    def settle(self):
        """Lets every task that is ready run (bounded: a few loop iterations)."""
        for _ in range(5):
            self.loop.run_until_complete(asyncio.sleep(0))

    def advance(self, seconds: int):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds
        ready = [fut for due, fut in self.waiters if due <= self.mono]
        self.waiters = [(due, fut) for due, fut in self.waiters if due > self.mono and not fut.done()]
        for fut in ready:
            if not fut.done():
                fut.set_result(None)
        self.settle()

    def at(self, seconds: int) -> datetime:
        """A naive local datetime `seconds` after now, as the API and the saved files carry it."""
        return self.wall + timedelta(seconds=seconds)


class _ClockType(type):
    # isinstance(x, datetime) must keep working for the real datetimes the code receives
    def __instancecheck__(cls, obj):
        return isinstance(obj, REAL_DATETIME)


def fake_datetime(clock: FakeClock):
    class FakeDatetime(REAL_DATETIME, metaclass=_ClockType):
        @classmethod
        def now(cls, tz=None):
            return clock.wall
    return FakeDatetime


@pytest.fixture
def clock(monkeypatch, tmp_path):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    fake = FakeClock(loop)

    async def fake_sleep(delay, result=None):
        fut = asyncio.get_running_loop().create_future()
        fake.waiters.append((fake.mono + delay, fut))
        return await fut

    monkeypatch.setattr(async_timer, 'asyncio', SimpleNamespace(sleep=fake_sleep, ensure_future=asyncio.ensure_future))
    monkeypatch.setattr(async_timer, 'datetime', fake_datetime(fake))
    monkeypatch.setattr(assets_model, 'datetime', fake_datetime(fake))
    # every save goes to tmp, never to the real assets or playlist files
    monkeypatch.setitem(cmdargs.__dict__, 'assets_file', tmp_path / 'assets.json')
    yield fake
    for task in asyncio.all_tasks(loop):
        task.cancel()
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
    asyncio.set_event_loop(asyncio.new_event_loop())


def asset(**extra):
    return {'url': 'https://example.com/a', 'duration': 5, 'media_type': 'web', **extra}


def saved_enabled(tmp_path, uuid):
    data = json.loads((tmp_path / 'assets.json').read_text())
    return next(a['enabled'] for a in data['assets'] if a['uuid'] == uuid)


# ── enable and disable at the due second ─────────────────────────────────────

def test_scheduled_enable_fires_at_the_due_second_and_not_before(clock, tmp_path):
    am = AssetsManager()
    am.append(asset(enabled=False, ena_date=clock.at(60)))
    clock.settle()
    uuid = am.assets[0].uuid
    clock.advance(59)
    assert am.assets[0].enabled is False and am.assets[0].ena_date is not None
    clock.advance(1)
    assert am.assets[0].enabled is True and am.assets[0].ena_date is None
    assert am.current.uuid == uuid
    assert saved_enabled(tmp_path, uuid) is True


def test_scheduled_disable_fires_at_the_due_second_and_parks_the_playlist(clock, tmp_path):
    am = AssetsManager()
    am.append(asset(enabled=True, dis_date=clock.at(30)))
    am.next_a()                                 # what the playback loop does when it starts
    clock.settle()
    uuid = am.assets[0].uuid
    assert am.current.uuid == uuid
    clock.advance(29)
    assert am.assets[0].enabled is True
    clock.advance(1)
    assert am.assets[0].enabled is False and am.assets[0].dis_date is None
    assert am.count_enabled() == 0 and am.current.uuid != uuid
    assert saved_enabled(tmp_path, uuid) is False


def test_enable_then_disable_follow_their_own_dates(clock):
    am = AssetsManager()
    am.append(asset(enabled=False, ena_date=clock.at(10), dis_date=clock.at(20)))
    clock.settle()
    clock.advance(9)
    assert am.assets[0].enabled is False
    clock.advance(1)
    assert am.assets[0].enabled is True
    clock.advance(9)
    assert am.assets[0].enabled is True
    clock.advance(1)
    assert am.assets[0].enabled is False


def test_a_date_already_past_applies_at_once_without_a_timer(clock):
    am = AssetsManager()
    past = clock.at(-5)
    am.append(asset(enabled=False, ena_date=past))
    assert am.assets[0].enabled is True and am.assets[0].ena_date is None
    am.append(asset(enabled=True, dis_date=past))
    assert am.assets[1].enabled is False and am.assets[1].dis_date is None


def test_a_new_date_replaces_the_old_schedule(clock):
    am = AssetsManager()
    am.append(asset(enabled=False, ena_date=clock.at(60)))
    clock.settle()
    am.assets[0].ena_date = clock.at(20)
    clock.settle()
    clock.advance(19)
    assert am.assets[0].enabled is False
    clock.advance(1)
    assert am.assets[0].enabled is True
    clock.advance(60)                           # the old due second passes: nothing happens twice
    assert am.assets[0].enabled is True


def test_a_cleared_date_cancels_the_schedule(clock):
    am = AssetsManager()
    am.append(asset(enabled=False, ena_date=clock.at(60)))
    clock.settle()
    am.assets[0].ena_date = None
    clock.settle()
    clock.advance(120)
    assert am.assets[0].enabled is False


# ── the playlist follows the schedule ────────────────────────────────────────

def test_a_parked_playlist_starts_when_its_asset_is_scheduled(clock):
    am = AssetsManager()
    am.append(asset(enabled=False, ena_date=clock.at(10)))
    clock.settle()
    uuid = am.assets[0].uuid
    clock.advance(9)
    assert am.current.uuid != uuid
    clock.advance(1)
    assert am.current.uuid == uuid


def test_a_scheduled_disable_is_skipped_at_the_next_rotation(clock):
    am = AssetsManager()
    am.append(asset(enabled=True, duration=3, dis_date=clock.at(5)))
    am.append(asset(enabled=True, duration=3))
    am.next_a()
    clock.settle()
    first, second = am.assets[0].uuid, am.assets[1].uuid
    assert am.current.uuid == first
    clock.advance(3)
    assert am.current.uuid == second           # the duration of the first one has run
    clock.advance(1)                           # t=4
    assert am.current.uuid == second
    clock.advance(1)                           # t=5: the first one is disabled by its schedule
    assert am.assets[0].enabled is False
    clock.advance(3)                           # t=8: the rotation skips it
    assert am.current.uuid == second
    clock.advance(3)
    assert am.current.uuid == second


# ── the Scheduler API ────────────────────────────────────────────────────────

@pytest.fixture
def scheduler_api(clock, monkeypatch, tmp_path):
    """The API module, with uploads kept in tmp: importing it builds the upload manager of the node's /var/lib."""
    import utils.constants as constants
    monkeypatch.setattr(constants, 'uploads_folder', tmp_path / 'uploaded')
    from api.ws.endpoints import WS  # noqa: F401  (the endpoints module must load first: api.scheduler imports it)
    import api.scheduler as api_scheduler
    return api_scheduler


def test_api_edit_sets_a_schedule_that_fires_at_its_due_second(clock, monkeypatch, scheduler_api):
    am = AssetsManager()
    am.append(asset(enabled=False))
    uuid = am.assets[0].uuid
    monkeypatch.setattr(scheduler_api, 'playlists_manager', SimpleNamespace(get=lambda pid: am))
    clock.loop.run_until_complete(scheduler_api.Scheduler.edit_asset(SimpleNamespace(), uuid, ena_date=clock.at(30)))
    clock.settle()
    assert am.assets[0].ena_date is not None
    clock.advance(29)
    assert am.assets[0].enabled is False
    clock.advance(1)
    assert am.assets[0].enabled is True


def test_api_edit_sets_a_disable_date_that_fires_at_its_due_second(clock, monkeypatch, scheduler_api):
    am = AssetsManager()
    am.append(asset(enabled=True))
    uuid = am.assets[0].uuid
    monkeypatch.setattr(scheduler_api, 'playlists_manager', SimpleNamespace(get=lambda pid: am))
    clock.loop.run_until_complete(scheduler_api.Scheduler.edit_asset(SimpleNamespace(), uuid, dis_date=clock.at(15)))
    clock.settle()
    clock.advance(14)
    assert am.assets[0].enabled is True
    clock.advance(1)
    assert am.assets[0].enabled is False


def test_api_add_with_empty_dates_means_no_schedule(clock, monkeypatch, scheduler_api):
    am = AssetsManager()
    monkeypatch.setattr(scheduler_api, 'playlists_manager', SimpleNamespace(get=lambda pid: am))
    clock.loop.run_until_complete(scheduler_api.Scheduler.add_url_assets(
        SimpleNamespace(), [{'url': 'https://example.com/a', 'media_type': 'web', 'enabled': True,
                            'ena_date': '', 'dis_date': ''}], playlist_id=None))
    clock.settle()
    assert len(am.assets) == 1
    assert am.assets[0].ena_date is None and am.assets[0].dis_date is None
    clock.advance(3600)
    assert am.assets[0].enabled is True


# ── copy and move between playlists ──────────────────────────────────────────

@pytest.fixture
def playlists(clock):
    from utils.models.playlists import PlaylistsManager
    manager = PlaylistsManager()
    return manager, manager.create('Source'), manager.create('Target')


def test_a_copy_keeps_the_schedule_and_fires_in_the_target(clock, playlists):
    manager, source, target = playlists
    source.append(asset(enabled=False, ena_date=clock.at(30)))
    clock.settle()
    created = manager.transfer_assets([source.assets[0].uuid], source.playlist_id, target.playlist_id, move=False)
    clock.settle()
    assert len(source.assets) == 1 and len(target.assets) == 1
    assert target.assets[0].uuid == created[0] and target.assets[0].ena_date is not None
    clock.advance(30)
    assert target.assets[0].enabled is True


def test_a_moved_asset_fires_only_in_the_target(clock, playlists, monkeypatch):
    manager, source, target = playlists
    source.append(asset(enabled=False, ena_date=clock.at(30)))
    clock.settle()
    manager.transfer_assets([source.assets[0].uuid], source.playlist_id, target.playlist_id, move=True)
    clock.settle()
    assert source.assets == [] and len(target.assets) == 1
    saves = []
    real_save = AssetsManager.save
    monkeypatch.setattr(AssetsManager, 'save',
                        lambda self, *a, **k: saves.append(1) if self is source else real_save(self, *a, **k))
    clock.advance(30)
    assert target.assets[0].enabled is True
    assert saves == []
