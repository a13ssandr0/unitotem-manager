"""The ticker's models: clamping, refusing what cannot be repaired, no CSS injection, sources."""
import pytest
from pydantic import ValidationError

from utils.models.ticker import (COUNT_MAX, COUNT_MIN, FONT_PERCENT_MAX, FONT_PX_MAX, FONT_PX_MIN, INTERVAL_MAX,
                                 INTERVAL_MIN, MAX_SEPARATOR, MAX_TEMPLATE, RANGE_MAX_SECONDS, RANGE_MIN_SECONDS,
                                 SPEED_DEFAULT, SPEED_MAX, SPEED_MIN, TickerSettings, TickerSource, window_key)


def test_defaults_are_off_and_sensible():
    s = TickerSettings()
    assert s.enabled is False and s.speed == SPEED_DEFAULT == 2.5
    assert s.select.mode == 'count' and s.clock.enabled is False and s.rows == 1
    assert s.mode == 'reserve' and s.position == 'bottom'


@pytest.mark.parametrize('given,expected', [(0, SPEED_MIN), (-5, SPEED_MIN), (1e9, SPEED_MAX), ('3', 3.0), (2.5, 2.5)])
def test_speed_is_clamped(given, expected):
    assert TickerSettings(speed=given).speed == expected


@pytest.mark.parametrize('given', ['fast', None, float('nan')])
def test_speed_that_is_not_a_number_is_refused(given):
    with pytest.raises(ValidationError):
        TickerSettings(speed=given)


def test_font_size_is_clamped_by_unit():
    assert TickerSettings(font_size=100).font_size == FONT_PERCENT_MAX
    assert TickerSettings(font_size=0.1).font_size == 1.0
    assert TickerSettings(font_size=1000, font_unit='px').font_size == FONT_PX_MAX
    assert TickerSettings(font_size=2, font_unit='px').font_size == FONT_PX_MIN
    assert TickerSettings(font_size=48, font_unit='px').font_size == 48


def test_font_size_follows_a_change_of_unit():
    s = TickerSettings(font_size=3)
    s.font_unit = 'px'
    assert s.font_size == FONT_PX_MIN


def test_selection_is_clamped():
    from utils.models.ticker import TickerSelect
    assert TickerSelect(count=0).count == COUNT_MIN
    assert TickerSelect(count=100000).count == COUNT_MAX
    assert TickerSelect(mode='range', seconds=1).seconds == RANGE_MIN_SECONDS
    assert TickerSelect(mode='range', seconds=10 ** 12).seconds == RANGE_MAX_SECONDS
    with pytest.raises(ValidationError):
        TickerSelect(mode='yesterday')


@pytest.mark.parametrize('colour', ['#fff', '#FFAA00', '#ff8800cc', 'rgb(1,2,3)', 'rgba(0, 0, 0, 0.5)', 'rgb(1 2 3 / 50%)',
                                    'hsl(120, 50%, 50%)', 'red', 'RebeccaPurple'])
def test_colours_accepted(colour):
    assert TickerSettings(color=colour, background=colour).color == colour


@pytest.mark.parametrize('colour', ['', 'red; background: url(http://x)', 'url(http://evil/x.png)', 'var(--x)',
                                    'rgb(1,2,3);}', '#12', 'expression(alert(1))', 'a' * 40, None, '"red"'])
def test_colours_that_could_inject_css_are_refused(colour):
    with pytest.raises(ValidationError):
        TickerSettings(color=colour)
    with pytest.raises(ValidationError):
        TickerSettings(background=colour)


def test_template_rules():
    assert TickerSettings(template='[b]{title}[/b]').template == '[b]{title}[/b]'
    for bad in ('', '   ', 'x' * (MAX_TEMPLATE + 1), 'a\x00b'):
        with pytest.raises(ValidationError):
            TickerSettings(template=bad)


def test_separator_is_cut_and_cleaned():
    assert len(TickerSettings(separator='-' * 100).separator) == MAX_SEPARATOR
    assert TickerSettings(separator=' |\n| ').separator == ' || '
    assert TickerSettings(separator=None).separator == ''


def test_locale():
    assert TickerSettings(locale='it-IT').locale == 'it-IT'
    assert TickerSettings(locale='').locale == ''
    with pytest.raises(ValidationError):
        TickerSettings(locale='en_GB; drop')


def test_rows_and_enums_are_refused_not_guessed():
    for bad in ({'rows': 3}, {'position': 'left'}, {'mode': 'float'}):
        with pytest.raises(ValidationError):
            TickerSettings(**bad)
    with pytest.raises(ValidationError):
        TickerSettings(clock={'rows': 5})


def test_roundtrip_through_json():
    s = TickerSettings(enabled=True, rows=2, template='{title}', clock={'enabled': True, 'seconds': True},
                       select={'mode': 'today'})
    assert TickerSettings.model_validate(s.model_dump()) == s


def test_source_url_must_be_http_or_https():
    assert TickerSource(url='https://example.org/feed.xml').url == 'https://example.org/feed.xml'
    for bad in ('ftp://x/y', 'file:///etc/passwd', 'javascript:alert(1)', 'example.org', '', 'http://', 'http://a b/',
                'https://x/\ny', 'http://' + 'a' * 3000):
        with pytest.raises(ValidationError):
            TickerSource(url=bad)


def test_source_interval_and_name():
    assert TickerSource(url='http://a.b/', interval=1).interval == INTERVAL_MIN
    assert TickerSource(url='http://a.b/', interval=10 ** 9).interval == INTERVAL_MAX
    assert TickerSource(url='http://a.b/').interval == 900
    assert len(TickerSource(url='http://a.b/', name='n' * 500).name) == 80
    assert TickerSource(url='http://news.example.org/rss').label == 'news.example.org'
    assert TickerSource(url='http://a.b/', name='  BBC ').label == 'BBC'


def test_window_key_is_like_the_keyboards():
    assert window_key('v1', 'HDMI-A-1') == 'v1|HDMI-A-1'
