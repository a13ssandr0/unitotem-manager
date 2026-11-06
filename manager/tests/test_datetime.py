"""Date and time: time zone and NTP through a FAKE timedate1, the API answers, the properties watch, and the process
following a zone change without re-import. No system bus is opened: any real D-Bus open fails the test."""
import asyncio
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from unitotem_node.models import timers as timers_model
from unitotem_node.models.timers import Timer
from unitotem_node.system import dbus_system, timer_actions, wake
from unitotem_node.api.date_time import DateTime, status_fields
from api.ws.responses import WSBroadcast, WSResponse

ZONES = ['Europe/Rome', 'America/New_York', 'Etc/GMT+5', 'UTC']
INJECTIONS = ['../../etc/passwd', 'Europe/Rome; reboot', 'Europe/Rome\n', '', 'europe/rome', 'Europe/Rome ',
              'Mars/Olympus', '/etc/localtime', 'Europe/Rome\x00', 'UTC\n/../../x']


class _Prop:
    """What `await proxy.<property>` gives in sdbus: an awaitable carrying the value."""

    def __init__(self, value):
        self.value = value

    def __await__(self):
        return self.value
        yield  # makes __await__ a generator (the value is the StopIteration result)


class FakeTimedate:
    def __init__(self):
        self.calls = []
        self.state = {'timezone': 'Europe/Rome', 'ntp': True, 'can_ntp': True, 'synchronized': True,
                      'list_fails': False}

    def proxy(self, _bus):
        return self

    @property
    def timezone(self):
        return _Prop(self.state['timezone'])

    @property
    def ntp(self):
        return _Prop(self.state['ntp'])

    @property
    def can_ntp(self):
        return _Prop(self.state['can_ntp'])

    @property
    def ntp_synchronized(self):
        return _Prop(self.state['synchronized'])

    async def list_timezones(self):
        self.calls.append(('ListTimezones',))
        if self.state['list_fails']:
            raise OSError('timedate1 not available')
        return list(ZONES)

    async def set_timezone(self, tz, interactive):
        self.calls.append(('SetTimezone', tz, interactive))
        self.state['timezone'] = tz

    async def set_ntp(self, enabled, interactive):
        self.calls.append(('SetNTP', enabled, interactive))
        self.state['ntp'] = enabled


@pytest.fixture
def fake_timedate(monkeypatch):
    fake = FakeTimedate()
    fake_bus = object()

    @contextmanager
    def fake_system_bus():
        yield fake_bus

    def real_bus_forbidden():
        raise AssertionError('the real system bus must not be opened by these tests')

    monkeypatch.setattr(dbus_system, '_system_bus', fake_system_bus)
    monkeypatch.setattr(dbus_system, '_get_timedated', fake.proxy)
    monkeypatch.setattr(dbus_system.sdbus, 'sd_bus_open_system', real_bus_forbidden)
    monkeypatch.setenv('TZ', 'Europe/Rome')
    time.tzset()
    yield fake
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def zone(monkeypatch):
    def use(name):
        monkeypatch.setenv('TZ', name)
        time.tzset()
    yield use
    monkeypatch.undo()
    time.tzset()


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


# ── zones ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', INJECTIONS + [None, 42])
def test_a_zone_outside_the_list_is_refused_and_never_reaches_the_bus(fake_timedate, bad):
    with pytest.raises(ValueError, match='Unknown time zone'):
        asyncio.run(dbus_system.set_timezone(bad))
    assert not [c for c in fake_timedate.calls if c[0] == 'SetTimezone']
    assert fake_timedate.state['timezone'] == 'Europe/Rome'


def test_a_listed_zone_is_set_with_exactly_that_string(fake_timedate):
    asyncio.run(dbus_system.set_timezone('America/New_York'))
    assert ('SetTimezone', 'America/New_York', False) in fake_timedate.calls
    assert fake_timedate.state['timezone'] == 'America/New_York'


def test_setting_the_zone_drops_a_TZ_override(fake_timedate):
    assert 'TZ' in os.environ
    asyncio.run(dbus_system.set_timezone('America/New_York'))
    assert 'TZ' not in os.environ


def test_list_is_sorted_and_always_has_utc(fake_timedate):
    zones = asyncio.run(dbus_system.list_timezones())
    assert zones == sorted(zones) and 'UTC' in zones and 'Europe/Rome' in zones


def test_list_falls_back_to_the_zoneinfo_index_when_timedated_is_down(fake_timedate, monkeypatch):
    fake_timedate.state['list_fails'] = True
    monkeypatch.setattr(dbus_system, '_zones_from_index', lambda: {'Europe/Rome', 'Etc/UTC'})
    assert asyncio.run(dbus_system.list_timezones()) == ['Etc/UTC', 'Europe/Rome', 'UTC']


def test_index_parser_reads_zones_and_links_by_their_field(tmp_path):
    index = tmp_path / 'tzdata.zi'
    index.write_text('# comment\nZ Europe/Rome 0:49:56 - LMT 1866 D 12\nL Etc/UTC UTC\nR Something 1 2\n')
    assert dbus_system._zones_from_index(index=str(index), tab=str(tmp_path / 'missing')) == {'Europe/Rome', 'UTC'}


def test_index_parser_falls_back_to_zone1970_tab(tmp_path):
    tab = tmp_path / 'zone1970.tab'
    tab.write_text('# comment\nIT\t+4154+01227\tEurope/Rome\tcomment\n')
    assert dbus_system._zones_from_index(index=str(tmp_path / 'missing'), tab=str(tab)) == {'Europe/Rome'}


# ── NTP and status ─────────────────────────────────────────────────────────

def test_ntp_is_refused_when_the_node_cannot_switch_it(fake_timedate):
    fake_timedate.state['can_ntp'] = False
    with pytest.raises(ValueError):
        asyncio.run(dbus_system.set_ntp(False))
    assert not [c for c in fake_timedate.calls if c[0] == 'SetNTP']


def test_ntp_is_set_when_possible(fake_timedate):
    asyncio.run(dbus_system.set_ntp(False))
    assert ('SetNTP', False, False) in fake_timedate.calls


def test_timedate_status_shape(fake_timedate):
    status = asyncio.run(dbus_system.timedate_status())
    assert status == {'timezone': 'Europe/Rome', 'ntp': True, 'can_ntp': True, 'synchronized': True}
    assert isinstance(status['timezone'], str) and isinstance(status['ntp'], bool)


def test_full_status_fields_and_types(fake_timedate):
    fields = asyncio.run(status_fields())
    assert set(fields) == {'timezone', 'ntp', 'can_ntp', 'synchronized', 'timezones', 'local_time', 'utc_offset'}
    assert isinstance(fields['timezones'], list) and all(isinstance(z, str) for z in fields['timezones'])
    assert isinstance(fields['utc_offset'], int)
    parsed = datetime.fromisoformat(fields['local_time'])
    assert parsed.utcoffset() == timedelta(seconds=fields['utc_offset'])


# ── API ────────────────────────────────────────────────────────────────────

def test_api_set_timezone_answers_with_the_new_state(fake_timedate):
    api = DateTime(None, None)
    result = asyncio.run(api.set_timezone('America/New_York'))
    assert isinstance(result, WSBroadcast)
    assert result.kwargs['timezone'] == 'America/New_York'
    assert 'Europe/Rome' in result.kwargs['timezones']


def test_api_refuses_a_bad_zone_with_an_error(fake_timedate):
    result = asyncio.run(DateTime(None, None).set_timezone('../../etc/passwd'))
    assert isinstance(result, WSResponse) and 'Unknown time zone' in result.kwargs['error']
    assert not [c for c in fake_timedate.calls if c[0] == 'SetTimezone']


def test_api_set_ntp_answers_with_the_new_state(fake_timedate):
    result = asyncio.run(DateTime(None, None).set_ntp(False))
    assert isinstance(result, WSBroadcast) and result.kwargs['ntp'] is False


def test_api_set_ntp_error_when_not_possible(fake_timedate):
    fake_timedate.state['can_ntp'] = False
    result = asyncio.run(DateTime(None, None).set_ntp(True))
    assert isinstance(result, WSResponse) and 'error' in result.kwargs


def test_api_status(fake_timedate):
    result = asyncio.run(DateTime(None, None).status())
    assert isinstance(result, WSBroadcast) and result.kwargs['timezone'] == 'Europe/Rome'


# ── watch ──────────────────────────────────────────────────────────────────

class _Watch:
    def __init__(self, items):
        self.items = items

    async def _iter(self):
        for item in self.items:
            yield item

    @property
    def properties_changed(self):
        return self._iter()


def test_watch_follows_a_zone_change_and_calls_back(fake_timedate, monkeypatch):
    signals = [
        ('org.freedesktop.timedate1', {'Timezone': 'America/New_York'}, []),
        ('org.freedesktop.timedate1', {'LocalRTC': True}, []),          # not one of ours: no callback
        ('org.freedesktop.timedate1', {'NTP': False}, []),              # no zone change: no tzset
    ]
    watch = _Watch(signals)
    monkeypatch.setattr(dbus_system, '_get_timedated', lambda bus: watch)
    monkeypatch.setattr(dbus_system.sdbus, 'sd_bus_open_system', lambda: object())
    tzsets, callbacks = [], []
    monkeypatch.setattr(time, 'tzset', lambda: tzsets.append(1))

    async def on_change():
        callbacks.append(1)

    asyncio.run(dbus_system.watch_timedate(on_change))
    assert len(tzsets) == 1
    assert len(callbacks) == 2


# ── the process follows the zone without re-import ─────────────────────────

def _timer(hour='7'):
    return Timer(action='reb', minute='0', hour=hour, day_of_month='*', month='*', day_of_week='*')


def test_a_zone_switch_moves_the_wake_and_the_timers_without_reimport(zone, monkeypatch):
    # 2026-11-06 00:00 in Rome is 2026-11-05 23:00 UTC, and in New York 2026-11-05 18:00 (both on standard time)
    instant = utc(2026, 11, 5, 23, 0)
    timer = _timer()

    zone('Europe/Rome')
    assert wake.next_occurrence('0 7 * * *', instant) == utc(2026, 11, 6, 6, 0)       # 07:00 CET
    assert timer.matches(datetime.fromtimestamp(utc(2026, 11, 6, 6, 0)))                 # local 07:00
    assert not timer.matches(datetime.fromtimestamp(utc(2026, 11, 6, 12, 0)))            # local 13:00

    zone('America/New_York')
    assert wake.next_occurrence('0 7 * * *', instant) == utc(2026, 11, 6, 12, 0)     # 07:00 EST
    assert timer.matches(datetime.fromtimestamp(utc(2026, 11, 6, 12, 0)))                # local 07:00
    assert not timer.matches(datetime.fromtimestamp(utc(2026, 11, 6, 6, 0)))             # local 01:00
    # datetime.now() follows the zone: naive local minus UTC is -5 h in New York, +1 h in Rome
    offset = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(offset - timedelta(hours=-5)) < timedelta(seconds=5)

    # the timers loop: the minute that the clock reports is the one that runs the timer, in the current zone
    timers = {timer.uuid: timer}
    monkeypatch.setattr(timers_model.timer_manager, 'timers', timers)
    ran = []

    async def executor(t):
        ran.append(t.uuid)

    def loop_step(at):
        clock = timer_actions.MinuteClock(datetime.fromtimestamp(at - 60))
        return asyncio.run(timer_actions.run_due(clock.minutes(datetime.fromtimestamp(at)), executor=executor))

    loop_step(utc(2026, 11, 6, 12, 0))                  # 07:00 New York: due
    assert ran == [timer.uuid]
    loop_step(utc(2026, 11, 6, 6, 0))                   # 01:00 New York: not due
    assert ran == [timer.uuid]

    zone('Europe/Rome')
    loop_step(utc(2026, 11, 6, 6, 0))                   # 07:00 Rome: due again, the zone went back
    assert ran == [timer.uuid, timer.uuid]
