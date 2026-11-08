"""Scheduled enable/disable of an asset, lookups that must not disturb its timers, and failed constructions."""
import asyncio
import gc
from datetime import datetime, timedelta

import pytest

from utils.models.assets import TIMERS, Asset, AssetsManager
from utils.models.command_line import cmdargs


@pytest.fixture(autouse=True)
def loop(monkeypatch, tmp_path):
    asyncio.set_event_loop(asyncio.new_event_loop())
    # every save goes to tmp, never to the real assets file
    monkeypatch.setitem(cmdargs.__dict__, 'assets_file', tmp_path / 'assets.json')
    yield
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0))


def run_for(seconds: float):
    """Lets the event loop run the timers for a bounded time."""
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(seconds))


def soon(ms=50):
    return datetime.now() + timedelta(milliseconds=ms)


def test_scheduled_enable_turns_the_asset_on_and_starts_the_playlist():
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': False, 'ena_date': soon(), 'duration': 5})
    assert not am.has_enabled()
    run_for(0.3)
    asset = am.assets[0]
    assert asset.enabled is True
    assert asset.ena_date is None
    assert am.current.uuid == asset.uuid       # the parked playlist wakes up


def test_scheduled_disable_turns_the_asset_off():
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': True, 'dis_date': soon(), 'duration': 5})
    assert am.has_enabled()
    run_for(0.3)
    assert am.assets[0].enabled is False
    assert am.assets[0].dis_date is None
    assert not am.has_enabled()


def test_scheduled_change_is_saved(tmp_path):
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': False, 'ena_date': soon(), 'duration': 5})
    run_for(0.3)
    assert '"enabled": true' in (tmp_path / 'assets.json').read_text()


def test_removed_asset_is_not_touched_by_its_schedule(monkeypatch):
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': False, 'ena_date': soon(), 'duration': 5})
    removed = am.assets[0]
    am.assets.clear()
    saves = []
    # pydantic models refuse new attributes on the instance: the method is replaced on the class for this test
    monkeypatch.setattr(AssetsManager, 'save', lambda *a, **k: saves.append(1))
    run_for(0.3)
    assert removed.enabled is True          # the object flips, but the playlist ignores it
    assert saves == []
    assert am.current.url != removed.url


def test_failed_lookup_by_uuid_keeps_the_timers_of_the_asset():
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': False, 'ena_date': soon(60_000), 'duration': 5})
    asset = am.assets[0]
    timers = TIMERS[asset.uuid]
    assert am.index(asset.uuid) == 0
    am.goto_a(asset.uuid)
    with pytest.raises(ValueError):
        am.index('f' * 32)
    gc.collect()
    assert TIMERS[asset.uuid] is timers


def test_duration_change_of_the_playing_asset_reaches_the_playlist():
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'enabled': True, 'duration': 60})
    asset = am.assets[0]
    am.next_a()                              # starts playback, as the playlist loop does
    assert am.current.uuid == asset.uuid
    asset.update_duration(2)
    run_for(0.3)
    assert am.assets[0].duration == 2
    assert am.current.uuid == asset.uuid     # the change is applied as a timer, not as a new step


def test_failed_construction_raises_no_unraisable_error(monkeypatch):
    seen = []
    monkeypatch.setattr('sys.unraisablehook', lambda info: seen.append(info))
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Asset(url='https://example.com', zoom=float('nan'))
    gc.collect()
    assert seen == []


def test_assets_assigned_to_the_playlist_report_to_it():
    am = AssetsManager()
    am.assets = [{'url': 'https://example.com/a', 'duration': 5}]
    assert am.assets[0]._owner is am
    am.append({'url': 'https://example.com/b', 'duration': 5})
    am.move(1, 0)
    assert all(a._owner is am for a in am.assets)


def test_owner_is_not_serialized():
    am = AssetsManager()
    am.append({'url': 'https://example.com/a', 'duration': 5})
    assert '_owner' not in am.assets[0].model_dump()
    assert '_owner' not in am.model_dump()['assets'][0]
