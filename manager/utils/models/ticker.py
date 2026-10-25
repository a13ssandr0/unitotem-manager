"""
The RSS ticker: what a window's bar looks like (`TickerSettings`) and where its headlines come from (`TickerSource`).

Numbers are clamped, never refused (a bar that is too fast or too small must never make a screen unusable and a typo
in a form must not lose the whole configuration); things that cannot be repaired (a URL that is not http/https, a colour
that is not a colour) are refused with a message the web UI shows.

# Why the default speed is 2.5 em/s (and the range 0.25 - 10 em/s)

The speed is in em per second (a multiple of the font size) so a crawl reads the same on a 720p panel and on a 4K one.
There is no standard for it, so the default comes from the little evidence there is:
* Digital signage practice (Xibo, Wirespring/invidis articles on "the infamous ticker"): 50-80 px/s at 1080p with
  24-32 px text, i.e. 1.6-3.3 em/s; sources agree that faster crawling lowers comprehension and that a viewer gets a
  few seconds of attention (Wirespring: 1.5-3 s for glance media), so the bar must stay legible and slow.
* A Japanese ergonomics study on LED signs (J. Japan Ergonomics Soc., speeds of 2-8 characters/s, viewing distance
  0.7-2.5 m): perceived speed rose steadily over the range and subjective readability dropped significantly at 8
  characters/s, little affected by distance. With an average glyph of ~0.5 em, 8 characters/s = 4 em/s, so the
  comfortable upper end is about 3-4 em/s.
* Reading research (Legge et al. 1985; Frontiers in Psychology 2016, doi 10.3389/fpsyg.2016.00127): the maximum rate
  for scrolling text of a trained reader is 200-300 words/min (~20-30 characters/s) in an ideal fixed window, so
  nothing can be read above ~6-8 em/s, and a crawl is read at a much lower rate than static text.
2.5 em/s (about 5 characters/s, 80 px/s at 1080p with 3 % text) sits in the middle of the signage range and well below
the 8 characters/s where readability fell. The user can go from a very slow 0.25 em/s (a long crawl for a distant
viewer) to 10 em/s (a ticker that only has to be glanced at).
"""
from __future__ import annotations

import re
import uuid
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

# ── limits (all clamped) ─────────────────────────────────────────────────────

SPEED_MIN, SPEED_MAX, SPEED_DEFAULT = 0.25, 10.0, 2.5      # em/s, see above
FONT_PERCENT_MIN, FONT_PERCENT_MAX = 1.0, 15.0              # % of the smaller side of the window
FONT_PX_MIN, FONT_PX_MAX = 8.0, 400.0
COUNT_MIN, COUNT_MAX = 1, 500
RANGE_MIN_SECONDS, RANGE_MAX_SECONDS = 60, 30 * 24 * 3600
INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 60, 24 * 3600, 15 * 60
MAX_TEMPLATE = 500
MAX_SEPARATOR = 20
MAX_SOURCES = 50
MAX_URL = 2048
MAX_NAME = 80

DEFAULT_TEMPLATE = '[b]{time:HH:mm}[/b] - {title}'

# A CSS colour the page may put in a style: hex, rgb()/rgba()/hsl()/hsla() with plain numbers, or a colour name.
# Nothing else gets through (no url(), no var(), no ';'), so a setting can never inject CSS.
_COLOUR_RE = re.compile(
    r'(?:#[0-9a-fA-F]{3,8}'
    r'|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.]+%?\s*[, ]\s*[0-9.]+%?\s*[, ]\s*[0-9.]+%?\s*(?:[,/]\s*[0-9.]+%?\s*)?\)'
    r'|[a-zA-Z]{3,24})')


def _num(value, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name}: {value!r} is not a number')
    if number != number:      # NaN
        raise ValueError(f'{name}: not a number')
    return min(high, max(low, number))


def _colour(value, name: str) -> str:
    value = '' if value is None else str(value).strip()
    if not _COLOUR_RE.fullmatch(value):
        raise ValueError(f'{name}: {value!r} is not a colour (use #rrggbb, rgb(), rgba() or a colour name)')
    return value


class TickerSelect(BaseModel, validate_assignment=True):
    """Which headlines the bar shows, decided by the page on every cycle with the device clock (items age out):
    the newest `count`, the ones of today (since local midnight) or the ones of the last `seconds`."""
    mode: Literal['count', 'today', 'range'] = 'count'
    count: int = 20
    seconds: int = 2 * 3600

    @field_validator('count', mode='before')
    @classmethod
    def _count(cls, v):
        return int(_num(v, COUNT_MIN, COUNT_MAX, 'count'))

    @field_validator('seconds', mode='before')
    @classmethod
    def _seconds(cls, v):
        return int(_num(v, RANGE_MIN_SECONDS, RANGE_MAX_SECONDS, 'seconds'))


class TickerClock(BaseModel, validate_assignment=True):
    """A clock fixed at the start or at the end of the bar (it does not scroll)."""
    enabled: bool = False
    position: Literal['start', 'end'] = 'end'
    seconds: bool = False
    date: bool = False
    rows: Literal[1, 2] = 1


class TickerSettings(BaseModel, validate_assignment=True):
    """The bar of one window: off unless asked for."""
    enabled: bool = False
    position: Literal['top', 'bottom'] = 'bottom'        # edge of the window's area
    mode: Literal['overlay', 'reserve'] = 'reserve'      # reserve: the panes shrink to leave room for the bar
    rows: Literal[1, 2] = 1                              # lanes crawling in parallel, headlines alternate between them
    font_size: float = 3.0
    font_unit: Literal['percent', 'px'] = 'percent'      # percent of the smaller side of the window, or pixels
    color: str = 'rgb(255,255,255)'
    background: str = 'rgba(0,0,0,0.85)'
    speed: float = SPEED_DEFAULT                         # em/s, see the top of this file
    separator: str = '   •   '
    template: str = DEFAULT_TEMPLATE
    select: TickerSelect = Field(default_factory=TickerSelect)
    clock: TickerClock = Field(default_factory=TickerClock)
    locale: str = ''                                     # BCP 47 tag for month/day names; empty = the device's

    @field_validator('font_size', mode='before')
    @classmethod
    def _font(cls, v):
        # the unit is not known here: clamp to the widest range, the model validator narrows it
        return _num(v, FONT_PERCENT_MIN, FONT_PX_MAX, 'font_size')

    @model_validator(mode='after')
    def _clamp_font(self):
        low, high = (FONT_PERCENT_MIN, FONT_PERCENT_MAX) if self.font_unit == 'percent' else (FONT_PX_MIN, FONT_PX_MAX)
        clamped = min(high, max(low, self.font_size))
        if clamped != self.font_size:
            object.__setattr__(self, 'font_size', clamped)      # not setattr: validate_assignment would recurse
        return self

    @field_validator('speed', mode='before')
    @classmethod
    def _speed(cls, v):
        return _num(v, SPEED_MIN, SPEED_MAX, 'speed')

    @field_validator('color', 'background', mode='before')
    @classmethod
    def _colours(cls, v, info):
        return _colour(v, info.field_name)

    @field_validator('template', mode='before')
    @classmethod
    def _template(cls, v):
        v = '' if v is None else str(v)
        if not v.strip():
            raise ValueError('template: the template is empty')
        if len(v) > MAX_TEMPLATE:
            raise ValueError(f'template: at most {MAX_TEMPLATE} characters')
        if any(ord(c) < 32 for c in v):
            raise ValueError('template: control characters are not allowed')
        return v

    @field_validator('separator', mode='before')
    @classmethod
    def _separator(cls, v):
        v = '' if v is None else str(v)
        return ''.join(c for c in v if ord(c) >= 32)[:MAX_SEPARATOR]

    @field_validator('locale', mode='before')
    @classmethod
    def _locale(cls, v):
        v = '' if v is None else str(v).strip()
        if v and not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}', v):
            raise ValueError(f'locale: {v!r} is not a language tag such as en-GB')
        return v

    def for_page(self) -> dict:
        """What the window's page receives."""
        return self.model_dump()


def _source_id() -> str:
    return uuid.uuid4().hex[:12]


def check_url(value) -> str:
    """A feed address: http or https with a host, no spaces or control characters, at most 2048 characters."""
    value = '' if value is None else str(value).strip()
    parts = urlsplit(value) if value else None
    try:
        host = parts.hostname if parts else None
    except ValueError:
        host = None
    if (not parts or len(value) > MAX_URL or parts.scheme.lower() not in ('http', 'https') or not host
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError('The address of a feed must be an http or https URL')
    return value


class TickerSource(BaseModel, validate_assignment=True):
    """One RSS/Atom feed. All the sources flow into one merged list."""
    id: str = Field(default_factory=_source_id, pattern=r'^[0-9a-f]{12}$')
    name: str = ''
    url: str
    interval: int = INTERVAL_DEFAULT          # seconds between two fetches, 60 s .. 1 day
    enabled: bool = True

    @field_validator('url', mode='before')
    @classmethod
    def _url(cls, v):
        return check_url(v)

    @field_validator('name', mode='before')
    @classmethod
    def _name(cls, v):
        v = '' if v is None else str(v)
        return ''.join(c for c in v if c.isprintable()).strip()[:MAX_NAME]

    @field_validator('interval', mode='before')
    @classmethod
    def _interval(cls, v):
        return int(_num(v, INTERVAL_MIN, INTERVAL_MAX, 'interval'))

    @property
    def label(self) -> str:
        return self.name or (urlsplit(self.url).hostname or self.url)


def window_key(viewer_id: str, screen_name: Optional[str]) -> str:
    """Key of a window's settings, like the keyboards of viewer_settings: the output name survives a replug."""
    return f'{viewer_id}|{screen_name}'
