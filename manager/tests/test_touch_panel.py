"""Touch panels: the settings models of the screensaver and of the scheduled refresh, and the idle logic."""
import asyncio
from datetime import datetime

import pytest
from pydantic import ValidationError

from unitotem_viewer.idle import REFRESH, SAVER, IdleState, IdleWatch, schedule_due, seconds_to_next_minute
from utils.models.viewer import (IDLE_MAX_SECONDS, IDLE_MIN_SECONDS, MAX_SCHEDULES, REFRESH_MIN_SECONDS,
                                 RefreshSettings, ScreensaverSettings, ViewerSettings, normalize_schedule)


def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M')


# ── screensaver settings ─────────────────────────────────────────────────

def test_screensaver_is_off_by_default():
    s = ScreensaverSettings()
    assert (s.enabled, s.home_url) == (False, '')


def test_screensaver_accepts_http_and_https_homes():
    assert ScreensaverSettings(enabled=True, home_url=' https://kiosk.example/app?x=1 ').home_url == \
        'https://kiosk.example/app?x=1'
    assert ScreensaverSettings(enabled=True, home_url='http://192.168.1.5:8080/').enabled


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'javascript:alert(1)', 'ftp://x/y', 'data:text/html,hi',
                                 'https://', 'not a url', 'example.com', 'https://a b/', 'https://x/\ny',
                                 'https://x/' + 'a' * 3000])
def test_screensaver_refuses_other_homes(url):
    with pytest.raises(ValidationError):
        ScreensaverSettings(enabled=True, home_url=url)


def test_enabled_screensaver_needs_a_home_page():
    with pytest.raises(ValidationError):
        ScreensaverSettings(enabled=True, home_url='')
    assert ScreensaverSettings(enabled=False, home_url='').enabled is False


@pytest.mark.parametrize('given, expected', [(0, IDLE_MIN_SECONDS), (-50, IDLE_MIN_SECONDS), (1, IDLE_MIN_SECONDS),
                                             (10 ** 9, IDLE_MAX_SECONDS), ('30', 30), (45.9, 45)])
def test_idle_timeout_is_clamped_never_refused(given, expected):
    assert ScreensaverSettings(enabled=True, home_url='https://a.b/', idle_timeout=given).idle_timeout == expected


def test_idle_timeout_that_is_not_a_number_is_refused():
    with pytest.raises(ValidationError):
        ScreensaverSettings(idle_timeout='soon')


# ── refresh settings ─────────────────────────────────────────────────────

def test_refresh_is_off_by_default():
    r = RefreshSettings()
    assert r.refresh_after_idle is None and r.refresh_schedules == []


def test_refresh_idle_empty_means_never_and_is_clamped():
    assert RefreshSettings(refresh_after_idle=0).refresh_after_idle is None
    assert RefreshSettings(refresh_after_idle='').refresh_after_idle is None
    assert RefreshSettings(refresh_after_idle=1).refresh_after_idle == REFRESH_MIN_SECONDS
    assert RefreshSettings(refresh_after_idle=600).refresh_after_idle == 600


def test_schedules_are_normalized_and_deduplicated():
    r = RefreshSettings(refresh_schedules=['0  6 * * *', '0 6 * * *', ' */15 * * * 1-5 '])
    assert r.refresh_schedules == ['0 6 * * *', '*/15 * * * 1-5']


@pytest.mark.parametrize('expression', ['', '* * * *', '* * * * * *', '60 * * * *', '* 24 * * *', '*/0 * * * *',
                                        'a b c d e', '0 6 * * *; reboot', '$(x) * * * *', '@daily'])
def test_invalid_schedules_are_refused(expression):
    with pytest.raises(ValidationError):
        RefreshSettings(refresh_schedules=[expression])
    with pytest.raises(ValueError):
        normalize_schedule(expression)


def test_too_many_schedules_are_refused():
    with pytest.raises(ValidationError):
        RefreshSettings(refresh_schedules=[f'{m} * * * *' for m in range(MAX_SCHEDULES + 1)])


def test_settings_survive_a_save_and_a_load(tmp_path):
    settings = ViewerSettings(
        screensavers={'v|HDMI-1': ScreensaverSettings(enabled=True, home_url='https://a.b/', idle_timeout=30)},
        refreshes={'v|HDMI-1': RefreshSettings(refresh_after_idle=60, refresh_schedules=['0 6 * * *'])})
    again = ViewerSettings(**settings.model_dump())
    assert again.screensavers['v|HDMI-1'].idle_timeout == 30
    assert again.refreshes['v|HDMI-1'].refresh_schedules == ['0 6 * * *']


# ── schedule evaluation ──────────────────────────────────────────────────

def test_schedule_due_matches_the_minute():
    assert schedule_due(['30 6 * * *'], at('2026-10-14 06:30'))
    assert not schedule_due(['30 6 * * *'], at('2026-10-14 06:31'))
    assert schedule_due(['0 1 * * *', '*/15 * * * *'], at('2026-10-14 09:45'))      # any of them
    assert not schedule_due([], at('2026-10-14 09:45'))


def test_schedule_due_with_weekdays():
    assert schedule_due(['0 8 * * 1-5'], at('2026-10-14 08:00'))        # a Wednesday
    assert not schedule_due(['0 8 * * 1-5'], at('2026-10-17 08:00'))    # a Saturday


def test_an_invalid_schedule_never_matches_and_never_raises():
    assert not schedule_due(['nonsense'], at('2026-10-14 06:30'))


def test_seconds_to_next_minute_lands_just_after_the_boundary():
    assert seconds_to_next_minute(datetime(2026, 10, 14, 6, 30, 0)) == pytest.approx(60.05)
    assert seconds_to_next_minute(datetime(2026, 10, 14, 6, 30, 59, 500000)) == pytest.approx(0.55)


# ── idle state ───────────────────────────────────────────────────────────

def test_nothing_is_due_without_settings():
    st = IdleState(0)
    assert st.next_deadline() is None and st.take_due(10 ** 6) == []


def test_screensaver_after_the_timeout_and_activity_postpones_it():
    st = IdleState(0)
    st.configure(60, None, 0)
    assert st.next_deadline() == 60
    st.touch(50)
    assert st.take_due(60) == []
    assert st.next_deadline() == 110
    assert st.take_due(110) == [SAVER]
    assert st.next_deadline() is None       # waiting for the page's answer


def test_screensaver_up_ignores_activity_until_dismissed():
    st = IdleState(0)
    st.configure(60, None, 0)
    assert st.take_due(60) == [SAVER]
    st.saver_result(True, 60)
    st.touch(100)                            # the page takes care of it
    assert st.next_deadline() is None
    st.dismissed(120)
    assert st.next_deadline() == 180


def test_screensaver_with_nothing_to_show_asks_again_after_a_timeout():
    st = IdleState(0)
    st.configure(60, None, 0)
    st.take_due(60)
    st.saver_result(False, 60)
    assert st.next_deadline() == 120


def test_refresh_on_idle_repeats_every_period_and_activity_postpones_it():
    st = IdleState(0)
    st.configure(None, 100, 0)
    assert st.take_due(100) == [REFRESH]
    assert st.next_deadline() == 200
    st.touch(150)
    assert st.take_due(200) == []
    assert st.take_due(250) == [REFRESH]


def test_a_screensaver_window_is_never_refreshed_on_idle():
    st = IdleState(0)
    st.configure(60, 30, 0)
    assert st.refresh_idle is None
    assert st.take_due(30) == []


# ── the timer around it ──────────────────────────────────────────────────

def test_idle_watch_fires_the_screensaver_and_rearms_after_dismissal():
    async def scenario():
        shown, refreshed = [], []

        async def show_saver():
            shown.append(1)
            return True

        async def refresh():
            refreshed.append(1)

        watch = IdleWatch(show_saver, refresh)
        watch.configure(0.2, None)
        await asyncio.sleep(0.1)
        watch.activity()                    # keeps it from firing at 0.2 s
        await asyncio.sleep(0.15)
        assert shown == []
        await asyncio.sleep(0.25)
        assert shown == [1]
        watch.activity()                    # ignored: the screensaver is up
        await asyncio.sleep(0.3)
        assert shown == [1]
        watch.dismissed()
        await asyncio.sleep(0.35)
        assert shown == [1, 1]
        watch.close()

    asyncio.run(scenario())


def test_idle_watch_refreshes_repeatedly_and_survives_a_failing_action():
    async def scenario():
        calls = []

        async def show_saver():
            return False

        async def refresh():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('the page is gone')

        watch = IdleWatch(show_saver, refresh)
        watch.configure(None, 0.1)
        await asyncio.sleep(0.45)
        watch.close()
        assert len(calls) >= 3

    asyncio.run(scenario())
