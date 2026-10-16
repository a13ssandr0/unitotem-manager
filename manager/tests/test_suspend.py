"""Suspend with a scheduled wake-up: wake time computation, validation, refusals on a fake sysfs, the timers loop policy
around a clock jump. Nothing here touches the real RTC or logind."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unitotem_node.models.timers import Timer
from unitotem_node.system import rtc, suspend, timer_actions, wake
from unitotem_node.system.rtc import SuspendRefused


@pytest.fixture
def rome(monkeypatch):
    monkeypatch.setenv('TZ', 'Europe/Rome')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def local(text):
    """Epoch of a local wall-clock time 'YYYY-MM-DD HH:MM' (first occurrence if ambiguous)."""
    return datetime.strptime(text, '%Y-%m-%d %H:%M').timestamp()


def shown(epoch):
    return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M')


# ── wake time ────────────────────────────────────────────────────────────────

def test_next_occurrence_is_strictly_after(rome):
    assert shown(wake.next_occurrence('0 7 * * *', local('2026-10-14 22:00'))) == '2026-10-15 07:00'
    assert shown(wake.next_occurrence('0 7 * * *', local('2026-10-14 06:00'))) == '2026-10-14 07:00'
    assert shown(wake.next_occurrence('0 7 * * *', local('2026-10-14 07:00'))) == '2026-10-15 07:00'


def test_weekday_schedule_skips_the_weekend(rome):
    friday = local('2026-10-16 22:00')
    assert shown(wake.next_occurrence('30 7 * * 1-5', friday)) == '2026-10-19 07:30'


def test_dst_spring_forward_keeps_the_wall_clock_time(rome):
    """The night of 2026-03-29 is 1 hour shorter: 07:00 is 7 hours after 23:00, not 8. (croniter given a tz-aware
    datetime answers 06:00 here, which is why wake.py feeds it naive local times.)"""
    start = local('2026-03-28 23:00')
    result = wake.next_occurrence('0 7 * * *', start)
    assert shown(result) == '2026-03-29 07:00'
    assert result - start == 7 * 3600                   # 8 h on the wall clock, 7 h of real time


def test_dst_fall_back_night_is_one_hour_longer(rome):
    start = local('2026-10-24 23:00')
    result = wake.next_occurrence('0 7 * * *', start)
    assert shown(result) == '2026-10-25 07:00'
    assert result - start == 9 * 3600


def test_dst_skipped_local_time_still_gives_a_future_instant(rome):
    start = local('2026-03-28 23:00')
    result = wake.next_occurrence('30 2 * * *', start)      # 02:30 does not exist on 2026-03-29
    assert start < result < start + 8 * 3600
    assert shown(result).startswith('2026-03-29')


def test_dst_repeated_local_time_picks_the_right_pass(rome):
    first_pass = wake.next_occurrence('30 2 * * *', local('2026-10-24 23:00'))
    assert first_pass - local('2026-10-24 23:00') == 3.5 * 3600         # 02:30 CEST, the first one
    # asleep at 02:10 of the second pass (CET): 02:30 CEST is already past, so it is the second 02:30 (CET)
    second_pass_start = local('2026-10-25 02:00') + 3600 + 600          # 02:10 CET
    assert shown(second_pass_start) == '2026-10-25 02:10'
    result = wake.next_occurrence('30 2 * * *', second_pass_start)
    assert result - second_pass_start == 20 * 60


def test_month_end(rome):
    assert shown(wake.next_occurrence('0 7 31 * *', local('2026-01-30 23:00'))) == '2026-01-31 07:00'
    assert shown(wake.next_occurrence('0 7 31 * *', local('2026-02-01 08:00'))) == '2026-03-31 07:00'
    assert shown(wake.next_occurrence('0 7 1 * *', local('2026-12-31 23:59'))) == '2027-01-01 07:00'
    assert shown(wake.next_occurrence('0 7 29 2 *', local('2026-03-01 00:00'))) == '2028-02-29 07:00'


def test_a_schedule_that_never_occurs_is_an_error(rome):
    with pytest.raises(wake.WakeError):
        wake.next_occurrence('0 7 30 2 *', local('2026-01-01 00:00'))


def test_sleep_too_short_or_too_long_is_refused(rome):
    now = local('2026-10-14 22:00')
    with pytest.raises(wake.WakeError, match='at least'):
        wake.plan_wake('1 22 * * *', now)               # one minute later
    with pytest.raises(wake.WakeError, match='at least'):
        wake.check_sleep(now, now + 30)
    with pytest.raises(wake.WakeError, match='days away'):
        wake.plan_wake('0 7 1 1 *', now)                # months away
    assert wake.plan_wake('0 7 * * *', now) == local('2026-10-15 07:00')
    assert wake.check_sleep(now, now + wake.MIN_SLEEP) == now + wake.MIN_SLEEP
    with pytest.raises(wake.WakeError):
        wake.check_sleep(now, now + wake.MIN_SLEEP - 1)
    with pytest.raises(wake.WakeError):
        wake.check_sleep(now, now + wake.MAX_SLEEP + 1)
    with pytest.raises(wake.WakeError):
        wake.check_sleep(now, now - 10)                 # in the past


def test_wake_in_the_past_never_happens_with_a_schedule(rome):
    """The next occurrence is by construction after the suspend time."""
    now = local('2026-10-14 22:00')
    assert wake.next_occurrence('0 21 * * *', now) > now


def test_check_pair_names_the_run_that_breaks(rome):
    now = local('2026-10-14 12:00')
    wake.check_pair('0 22 * * *', '0 7 * * *', now)             # every night: fine
    with pytest.raises(wake.WakeError, match='Suspending at'):
        wake.check_pair('59 6 * * *', '0 7 * * *', now)         # suspends one minute before the wake-up
    with pytest.raises(wake.WakeError):
        wake.check_pair('* * * * *', '0 7 * * *', now)          # suspends every minute: one is too close
    with pytest.raises(wake.WakeError, match='days away'):
        wake.check_pair('0 22 * * *', '0 7 1 * *', now)         # waking once a month


def test_parse_local(rome):
    assert wake.parse_local('2026-10-15T07:00') == local('2026-10-15 07:00')
    assert wake.parse_local('2026-10-15T07:00+00:00') == datetime(2026, 10, 15, 7, tzinfo=timezone.utc).timestamp()
    for bad in ('', 'tomorrow', '2026-13-01T00:00', None, '2026-10-15T07:00; reboot'):
        with pytest.raises(wake.WakeError):
            wake.parse_local(bad)


# ── timers model and backup compatibility ────────────────────────────────────

def sus(**kw):
    return Timer(**{'action': 'sus', 'minute': '0', 'hour': '22', 'day_of_month': '*', 'month': '*',
                    'day_of_week': '*', 'wake': '0 7 * * *', **kw})


def test_suspend_timer_needs_a_valid_wake_schedule():
    assert sus().wake == '0 7 * * *'
    assert sus(wake='  0   7 * * 1-5 ').wake == '0 7 * * 1-5'
    for bad in (None, '', '0 7 * *', '0 7 * * * *', '61 7 * * *', '0 7 * * *; reboot', 'rm -rf /', 5):
        with pytest.raises(ValueError):
            sus(wake=bad)


def test_other_actions_do_not_report_a_wake():
    t = Timer(action='reb', minute='0', hour='3', day_of_month='*', month='*', day_of_week='*')
    assert t.wake is None and t.serialize()['wake'] is None


def test_old_timers_without_the_new_field_still_load():
    old = {'uuid': 'a' * 32, 'action': 'reb', 'minute': '0', 'hour': '3', 'day_of_month': '*', 'month': '*',
           'day_of_week': '*', 'enabled': True, 'output': None, 'mechanism': 'auto'}
    assert Timer(**old).wake is None


def test_suspend_timer_round_trips_through_the_backup_format():
    t = sus()
    again = Timer(**json.loads(json.dumps(t.model_dump())))       # what _export_timers / _restore_timers do
    assert again == t and again.action == 'sus' and again.wake == '0 7 * * *'


def test_serialize_reports_the_next_sleep_and_wake(rome):
    s = sus().serialize()
    assert s['command'] == 'sus' and s['wake_problem'] is None
    assert s['next_wake'] - s['next_suspend'] >= wake.MIN_SLEEP
    bad = sus(hour='6', minute='59').serialize()
    assert bad['next_wake'] is None and 'at least' in bad['wake_problem']


# ── the RTC on a fake sysfs ──────────────────────────────────────────────────

NOW = 1_800_000_000


def make_rtc(root: Path, name='rtc0', epoch=NOW, wakealarm=True, wakeup='enabled'):
    d = root / name
    d.mkdir(parents=True)
    (d / 'since_epoch').write_text(f'{epoch}\n')
    if wakealarm:
        (d / 'wakealarm').write_text('\n')
    if wakeup is not None:
        (d / 'device' / 'power').mkdir(parents=True)
        (d / 'device' / 'power' / 'wakeup').write_text(wakeup + '\n')
    return d


def adjtime(tmp_path, mode):
    f = tmp_path / 'adjtime'
    f.write_text(f'0.0 0 0.0\n0\n{mode}\n')
    return f


def test_no_rtc_at_all(tmp_path):
    with pytest.raises(SuspendRefused, match='no real-time clock'):
        rtc.find_rtc(tmp_path / 'missing', tmp_path / 'adjtime', NOW)
    (tmp_path / 'empty').mkdir()
    with pytest.raises(SuspendRefused, match='no real-time clock'):
        rtc.find_rtc(tmp_path / 'empty', tmp_path / 'adjtime', NOW)


def test_rtc_without_wakealarm(tmp_path):
    make_rtc(tmp_path, wakealarm=False)
    with pytest.raises(SuspendRefused, match='cannot wake the machine'):
        rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)


def test_rtc_not_wakeup_capable(tmp_path):
    make_rtc(tmp_path, wakeup='disabled')
    with pytest.raises(SuspendRefused, match='not allowed to wake'):
        rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)


def test_rtc_without_wakeup_attribute_is_accepted_and_verified_by_readback(tmp_path):
    make_rtc(tmp_path, wakeup=None)
    assert rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW).name == 'rtc0'


def test_a_later_rtc_is_used_when_the_first_cannot_wake(tmp_path):
    make_rtc(tmp_path, 'rtc0', wakealarm=False)
    make_rtc(tmp_path, 'rtc1')
    assert rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW).name == 'rtc1'


def test_utc_rtc_alarm_is_the_epoch_and_is_read_back(tmp_path):
    d = make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, adjtime(tmp_path, 'UTC'), NOW)
    assert found.shift == 0
    assert rtc.arm(found, NOW + 3600, NOW) == NOW + 3600
    assert (d / 'wakealarm').read_text().strip() == str(NOW + 3600)
    assert found.current_alarm() == NOW + 3600
    rtc.clear(found)
    assert found.current_alarm() is None


def test_small_drift_is_absorbed_into_the_alarm(tmp_path):
    make_rtc(tmp_path, epoch=NOW + 40)          # the RTC runs 40 s fast: its alarm must be 40 s later in its own frame
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    assert found.alarm_for(NOW + 3600) == NOW + 3600 + 40


def test_local_time_rtc_is_measured_not_assumed(tmp_path, rome):
    """Hardware holding local time (dual boot): the kernel reads it as UTC, so it reads UTC offset seconds ahead."""
    now = local('2026-10-14 22:00')
    offset = rtc.local_utc_offset(now)
    assert offset == 7200
    make_rtc(tmp_path, epoch=int(now) + offset)
    found = rtc.find_rtc(tmp_path, adjtime(tmp_path, 'LOCAL'), now)
    assert found.shift == 7200
    wake_at = local('2026-10-15 07:00')
    assert found.alarm_for(wake_at) == int(wake_at) + 7200


def test_rtc_that_contradicts_adjtime_is_refused(tmp_path, rome):
    now = local('2026-10-14 22:00')
    make_rtc(tmp_path, epoch=int(now) + 7200)           # holds local time...
    with pytest.raises(SuspendRefused, match='holds UTC'):
        rtc.find_rtc(tmp_path, adjtime(tmp_path, 'UTC'), now)       # ...but adjtime says UTC
    (tmp_path / 'rtc0').rename(tmp_path / 'old')
    make_rtc(tmp_path, epoch=int(now))                  # holds UTC...
    with pytest.raises(SuspendRefused, match='holds local time'):
        rtc.find_rtc(tmp_path, adjtime(tmp_path, 'LOCAL'), now)     # ...but adjtime says LOCAL


def test_rtc_far_from_the_system_clock_is_refused(tmp_path):
    make_rtc(tmp_path, epoch=NOW - 86400)               # e.g. a dead battery that reset the date
    with pytest.raises(SuspendRefused, match='wrong'):
        rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)


def test_arm_refuses_a_readback_mismatch_and_leaves_no_alarm(tmp_path, monkeypatch):
    d = make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    monkeypatch.setattr(rtc.Rtc, 'current_alarm', lambda self: 12345)
    with pytest.raises(SuspendRefused, match='did not keep'):
        rtc.arm(found, NOW + 3600, NOW)
    assert (d / 'wakealarm').read_text().strip() == '0'


def test_arm_refuses_when_the_kernel_refuses_the_write(tmp_path):
    d = make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    (d / 'wakealarm').unlink()
    (d / 'wakealarm').mkdir()                           # writing a directory fails like EINVAL/EBUSY would
    with pytest.raises(SuspendRefused):
        rtc.arm(found, NOW + 3600, NOW)


def test_arm_refuses_a_time_not_in_the_future(tmp_path):
    make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    with pytest.raises(SuspendRefused, match='future'):
        rtc.arm(found, NOW - 5, NOW)


# ── suspending, with logind and the clock faked ──────────────────────────────

@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake machine: sysfs in tmp_path, logind answering `answer`, and a record of what was asked of it."""
    real_now = time.time()
    d = make_rtc(tmp_path, epoch=int(real_now))
    log = {'answer': 'yes', 'suspended': 0, 'fail': None}

    async def can_suspend():
        return log['answer']

    async def system_suspend():
        log['suspended'] += 1
        log['alarm_at_suspend'] = (d / 'wakealarm').read_text().strip()
        if log['fail']:
            raise RuntimeError(log['fail'])

    monkeypatch.setattr(suspend.dbus_system, 'can_suspend', can_suspend)
    monkeypatch.setattr(suspend.dbus_system, 'system_suspend', system_suspend)
    monkeypatch.setattr(rtc, 'SYSFS', tmp_path)
    monkeypatch.setattr(rtc, 'ADJTIME', tmp_path / 'adjtime')
    suspend._armed = None
    suspend.last_refusal = None
    log['dir'] = d
    return log


def test_suspend_arms_the_alarm_then_suspends(machine):
    wake_at = time.time() + 3600
    asyncio.run(suspend.suspend_until(wake_at))
    assert machine['suspended'] == 1
    assert abs(int(machine['alarm_at_suspend']) - round(wake_at)) <= 1      # armed BEFORE the suspend request
    assert suspend._armed is not None


@pytest.mark.parametrize('answer', ['no', 'na', 'challenge', 'maybe'])
def test_suspend_is_refused_when_logind_does_not_say_yes(machine, answer):
    machine['answer'] = answer
    with pytest.raises(SuspendRefused, match='cannot suspend'):
        asyncio.run(suspend.suspend_until(time.time() + 3600))
    assert machine['suspended'] == 0
    assert (machine['dir'] / 'wakealarm').read_text().strip() == ''      # nothing was armed
    assert answer in suspend.last_refusal['reason']


def test_suspend_is_refused_without_a_usable_rtc(machine):
    (machine['dir'] / 'wakealarm').unlink()
    with pytest.raises(SuspendRefused, match='cannot wake the machine'):
        asyncio.run(suspend.suspend_until(time.time() + 3600))
    assert machine['suspended'] == 0


def test_suspend_is_refused_when_the_wake_time_is_too_close_or_far(machine):
    with pytest.raises(SuspendRefused, match='at least'):
        asyncio.run(suspend.suspend_until(time.time() + 30))
    with pytest.raises(SuspendRefused, match='days away'):
        asyncio.run(suspend.suspend_until(time.time() + 30 * 86400))
    assert machine['suspended'] == 0


def test_failed_suspend_request_clears_the_alarm(machine):
    machine['fail'] = 'Access denied'
    with pytest.raises(SuspendRefused, match='Access denied'):
        asyncio.run(suspend.suspend_until(time.time() + 3600))
    assert (machine['dir'] / 'wakealarm').read_text().strip() == '0'
    assert suspend._armed is None


def test_resume_clears_the_alarm_wakes_the_screens_and_nudges_the_timers(machine):
    asyncio.run(suspend.suspend_until(time.time() + 3600))
    woken = []

    async def wake_screens():
        woken.append(True)

    async def run():
        suspend.resumed.clear()
        await suspend.on_prepare_for_sleep(True, wake_screens)
        assert not suspend.resumed.is_set() and not woken
        await suspend.on_prepare_for_sleep(False, wake_screens)
        await asyncio.sleep(0)
        assert suspend.resumed.is_set()
        assert woken == [True]

    asyncio.run(run())
    assert suspend._armed is None
    assert (machine['dir'] / 'wakealarm').read_text().strip() == '0'


def test_support_reports_the_reason(machine):
    assert asyncio.run(suspend.support())['available'] is True
    machine['answer'] = 'no'
    s = asyncio.run(suspend.support())
    assert s['available'] is False and 'CanSuspend: no' in s['reason']


# ── the timers loop around a clock jump ──────────────────────────────────────

def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')


def minutes(clock, text, resumed=False):
    return [(m.strftime('%H:%M'), c) for m, c in clock.minutes(at(text), resumed)]


def test_clock_normal_ticks():
    c = timer_actions.MinuteClock(at('2026-10-14 21:59:30'))
    assert minutes(c, '2026-10-14 22:00:00') == [('22:00', False)]
    assert minutes(c, '2026-10-14 22:00:30') == []              # the same minute is never evaluated twice
    assert minutes(c, '2026-10-14 22:01:00') == [('22:01', False)]


def test_clock_jump_forward_without_sleep_runs_only_the_current_minute():
    c = timer_actions.MinuteClock(at('2026-10-14 10:00:10'))
    assert minutes(c, '2026-10-14 10:47:03') == [('10:47', False)]          # no burst of 46 minutes
    assert minutes(c, '2026-10-14 10:48:00') == [('10:48', False)]


def test_clock_going_back_never_replays():
    c = timer_actions.MinuteClock(at('2026-10-14 10:30:10'))
    assert minutes(c, '2026-10-14 10:10:00') == []
    assert minutes(c, '2026-10-14 10:10:40') == []
    assert minutes(c, '2026-10-14 10:11:00') == [('10:11', False)]


def test_resume_runs_the_current_minute_and_a_few_missed_ones():
    c = timer_actions.MinuteClock(at('2026-10-14 22:00:05'))
    assert minutes(c, '2026-10-15 07:00:41', resumed=True) == [
        ('06:55', True), ('06:56', True), ('06:57', True), ('06:58', True), ('06:59', True), ('07:00', False)]
    # 07:00 is due at wake time (alarm at 07:00:00, resume done at 07:00:41): it is not skipped
    assert minutes(c, '2026-10-15 07:01:00') == [('07:01', False)]


def test_resume_within_the_same_minute_does_nothing_twice():
    c = timer_actions.MinuteClock(at('2026-10-14 22:00:55'))
    assert minutes(c, '2026-10-14 22:00:58', resumed=True) == []


def test_resume_one_minute_later_is_a_normal_tick():
    c = timer_actions.MinuteClock(at('2026-10-14 22:00:55'))
    assert minutes(c, '2026-10-14 22:01:20', resumed=True) == [('22:01', False)]


def test_timers_due_during_sleep_run_once_and_suspend_is_never_replayed(monkeypatch):
    """Wake at 07:00:41. Due in the missed window: reboot-like 06:58, screen 07:00, and the nightly suspend of 06:59
    (an odd schedule, but the rule must hold): the suspend is skipped, the rest runs once, oldest first."""
    ran = []

    def timer(action, minute, hour, **kw):
        return Timer(action=action, minute=minute, hour=hour, day_of_month='*', month='*', day_of_week='*', **kw)

    from unitotem_node.models.timers import timer_manager
    saved = timer_manager.timers
    timer_manager.timers = {}
    for t in (timer('reb', '58', '6'), timer('sus', '59', '6', wake='0 8 * * *'),
              timer('scr_on', '0', '7', output='DP-1'), timer('reb', '30', '22')):
        timer_manager.timers[t.uuid] = t
    monkeypatch.setattr(type(timer_manager), 'save', lambda self: None)

    async def fake(t):
        ran.append((t.action, t.minute, t.hour))

    clock = timer_actions.MinuteClock(at('2026-10-14 22:00:05'))
    try:
        asyncio.run(timer_actions.run_due(clock.minutes(at('2026-10-15 07:00:41'), True), fake))
    finally:
        timer_manager.timers = saved
    assert ran == [('reb', '58', '6'), ('scr_on', '0', '7')]


def test_resume_event_ends_the_minute_wait_early():
    """run_timers waits for the minute boundary on resumed.wait(): the signal handler must wake it at once."""
    async def run():
        suspend.resumed.clear()
        waiter = asyncio.create_task(asyncio.wait_for(suspend.resumed.wait(), 60))
        await asyncio.sleep(0)
        t0 = time.monotonic()
        suspend.resumed.set()
        await waiter
        suspend.resumed.clear()
        return time.monotonic() - t0

    assert asyncio.run(run()) < 1


# ── the subscription loop (the D-Bus proxy itself is only provable on a machine with a real logind) ───────────────

class _FakeBus:
    closed = 0

    def close(self):
        _FakeBus.closed += 1


def test_watch_sleep_dispatches_every_signal_and_reconnects(monkeypatch):
    """Python-sdbus cannot be pointed at a private bus here (sd_bus_open_system ignores DBUS_SYSTEM_BUS_ADDRESS), so
    logind's signal stream is replaced by an async iterator; what is proved is the loop: order, error isolation, close,
    and reconnection after the stream ends."""
    seen, connections = [], []

    class Signal:
        def __init__(self, items):
            self.items = items

        async def __aiter__(self):
            for item in self.items:
                yield item

    class Logind:
        def __init__(self, n):
            self.prepare_for_sleep = Signal([True, False] if n == 1 else [True])

    def get_logind(bus):
        connections.append(bus)
        return Logind(len(connections))

    async def handler(starting, wake_screens=None):
        seen.append(starting)
        if len(seen) == 2:
            raise RuntimeError('a failing handler must not end the subscription')

    monkeypatch.setattr(suspend.dbus_system, 'get_logind', get_logind)
    monkeypatch.setattr(suspend.sdbus, 'sd_bus_open_system', lambda: _FakeBus())
    monkeypatch.setattr(suspend, 'on_prepare_for_sleep', handler)

    async def run():
        task = asyncio.create_task(suspend.watch_sleep(wake_screens=None, retry=0.01))
        for _ in range(100):
            if len(seen) >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()

    _FakeBus.closed = 0
    asyncio.run(run())
    assert seen[:3] == [True, False, True]          # the second connection (after the stream ended) delivers again
    assert len(connections) >= 2 and _FakeBus.closed >= 1


def test_check_pair_returns_the_longest_sleep(rome):
    longest = wake.check_pair('0 22 * * *', '0 7 * * *', local('2026-10-14 12:00'))
    assert longest == 9 * 3600          # 22:00 -> 07:00, except on the night DST ends... not within 8 days of October 14


def test_check_reach_tries_the_alarm_and_leaves_none_behind(tmp_path):
    d = make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    rtc.check_reach(found, 3600, NOW)
    assert found.current_alarm() is None
    (d / 'wakealarm').write_text('1800000999\n')          # somebody else's alarm: not touched, nothing tested
    rtc.check_reach(found, 3600, NOW)
    assert found.current_alarm() == 1800000999


def test_check_reach_reports_a_clock_that_cannot_hold_the_alarm(tmp_path, monkeypatch):
    d = make_rtc(tmp_path)
    found = rtc.find_rtc(tmp_path, tmp_path / 'adjtime', NOW)
    real = d / 'wakealarm'

    class Limited:
        """What the CMOS RTC of the test VM does: EIO for an alarm a day or more ahead, accepts 0 (clear)."""
        def read_text(self):
            return real.read_text()

        def exists(self):
            return True

        def write_text(self, text):
            if text.strip() != '0':
                raise OSError(5, 'Input/output error')
            real.write_text(text)

    monkeypatch.setattr(rtc.Rtc, 'alarm_file', property(lambda self: Limited()))
    with pytest.raises(SuspendRefused, match='24 hours'):
        rtc.check_reach(found, 30 * 3600, NOW)


# ── notifications ────────────────────────────────────────────────────────────

def raised():
    from utils.notifications import notifications
    return [n for n in notifications.snapshot(['admin']) if n['source'] == 'timers']


def test_a_refused_timer_suspend_raises_one_admin_notification_and_a_good_one_resolves_it(machine):
    machine['answer'] = 'no'
    for _ in range(2):
        with pytest.raises(SuspendRefused):
            asyncio.run(suspend.suspend_until(time.time() + 3600, timer='abc'))
    [one] = raised()
    assert one['level'] == 'warning' and one['audience'] == 'admin' and 'CanSuspend: no' in one['text']
    machine['answer'] = 'yes'
    asyncio.run(suspend.suspend_until(time.time() + 3600, timer='abc'))
    assert raised() == []


def test_a_refusal_asked_by_hand_is_not_a_notification(machine):
    machine['answer'] = 'no'
    with pytest.raises(SuspendRefused):
        asyncio.run(suspend.suspend_until(time.time() + 3600))
    assert raised() == []


def test_a_bad_wake_schedule_and_a_failed_request_are_notified_too(machine):
    with pytest.raises(SuspendRefused):
        asyncio.run(suspend.suspend_with_schedule('not a schedule', 'abc'))
    assert len(raised()) == 1
    machine['fail'] = 'Access denied'
    with pytest.raises(SuspendRefused):
        asyncio.run(suspend.suspend_until(time.time() + 3600, timer='abc'))
    [one] = raised()
    assert 'Access denied' in one['text']


def test_a_suspend_skipped_after_a_resume_is_notified():
    from unitotem_node.models.timers import timer_manager
    t = Timer(action='sus', minute='59', hour='6', day_of_month='*', month='*', day_of_week='*', wake='0 8 * * *')
    saved, timer_manager.timers = timer_manager.timers, {t.uuid: t}
    clock = timer_actions.MinuteClock(at('2026-10-14 22:00:05'))
    ran = []

    async def fake(timer):
        ran.append(timer)

    try:
        asyncio.run(timer_actions.run_due(clock.minutes(at('2026-10-15 07:00:41'), True), fake))
    finally:
        timer_manager.timers = saved
    assert ran == []
    [one] = raised()
    assert '06:59' in one['text']


def test_update_availability_follows_the_checks():
    from unitotem_node.system import updater
    from utils.notifications import notifications
    saved = dict(updater.availability)
    try:
        updater.record({'supported': True, 'available': True, 'version': '9.9.9'})
        [one] = notifications.snapshot(['admin'])
        assert one['title'] == 'Update available' and '9.9.9' in one['text']
        assert notifications.snapshot(['scheduler']) == []
        updater.record({'supported': True, 'error': 'no network'})       # a failed check changes nothing
        assert len(notifications.snapshot(['admin'])) == 1
        updater.record({'supported': True, 'available': False})
        assert notifications.snapshot(['admin']) == []
    finally:
        updater.availability.update(saved)
