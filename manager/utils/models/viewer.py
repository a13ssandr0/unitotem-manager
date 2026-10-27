"""
Settings of this node's own viewer, persisted across restarts.

Kept apart from the playlists and from the viewer -> playlist assignments:
those describe *what* is shown and *where*, this one describes how the local
browser itself behaves, and it applies to every window of this node.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from utils.models.command_line import cmdargs


class KeyboardSettings(BaseModel, validate_assignment=True):
    """The on-screen keyboard of one window (a touch panel): on unless switched off."""
    enabled: bool = True
    # where the button that opens it is, on the screen
    button_position: Literal['top-left', 'top', 'top-right', 'left', 'right',
                             'bottom-left', 'bottom', 'bottom-right'] = 'bottom-right'
    # a layout of Gboard: a language ('it', 'pt-BR'...) and, for a language that has several, one of its variants ('de/de_neo2'); the old
    # names ('english', 'italian'...) still mean the same (webui/src/keyboard-layouts.js)
    layout: str = Field(default='english', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{1,39}(/[A-Za-z0-9][A-Za-z0-9_.-]{0,39})?$')


# Idle times are clamped, not refused: a zero or a huge number must never leave a screen unusable
IDLE_MIN_SECONDS = 5
IDLE_MAX_SECONDS = 24 * 3600
REFRESH_MIN_SECONDS = 10
REFRESH_MAX_SECONDS = 7 * 24 * 3600
MAX_SCHEDULES = 24
MAX_URL_LENGTH = 2048
# one cron field: digits, lists, ranges, steps, names (jan, mon...), '*', '?' and the L/W/# extensions of croniter
_CRON_FIELD_RE = re.compile(r'^[0-9A-Za-z*/,\-?#]{1,64}$')


def _clamp(value, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        raise ValueError(f'{value!r} is not a number of seconds')


def normalize_schedule(expression: str) -> str:
    """One cron-style schedule (minute hour day-of-month month day-of-week) in its canonical spelling, or ValueError.

    The structure is checked here, which needs nothing but the standard library, because the core also runs on a server
    without the viewer; where croniter is installed (every machine with a viewer) the values are checked as well.
    """
    if not isinstance(expression, str):
        raise ValueError(f'{expression!r} is not a schedule')
    fields = expression.split()
    if len(fields) != 5 or any(not _CRON_FIELD_RE.match(f) for f in fields):
        raise ValueError(f'{expression!r} is not a schedule of five fields (minute hour day month weekday)')
    normalized = ' '.join(fields)
    try:
        from croniter import croniter
    except ImportError:
        return normalized
    if not croniter.is_valid(normalized):
        raise ValueError(f'{expression!r} is not a valid schedule')
    return normalized


class ScreensaverSettings(BaseModel, validate_assignment=True):
    """An interactive window (a touch panel): it shows `home_url` indefinitely and, after `idle_timeout` seconds
    without activity, the playlist of the window is played on top of it as a screensaver. Off unless asked for."""
    enabled: bool = False
    home_url: str = ''
    idle_timeout: int = 120

    @field_validator('home_url', mode='before')
    @classmethod
    def _url(cls, v):
        v = '' if v is None else str(v).strip()
        if not v:
            return ''
        parts = urlsplit(v)
        if (len(v) > MAX_URL_LENGTH or parts.scheme.lower() not in ('http', 'https') or not parts.hostname
                or any(c.isspace() or ord(c) < 32 for c in v)):
            raise ValueError('The home page must be an http or https address')
        return v

    @field_validator('idle_timeout', mode='before')
    @classmethod
    def _timeout(cls, v):
        return _clamp(v, IDLE_MIN_SECONDS, IDLE_MAX_SECONDS)

    @model_validator(mode='after')
    def _needs_a_page(self):
        if self.enabled and not self.home_url:
            raise ValueError('The screensaver needs a home page')
        return self


class RefreshSettings(BaseModel, validate_assignment=True):
    """Programmable reload of a web page shown indefinitely (a window without a screensaver): after `refresh_after_idle`
    seconds without activity and/or at the minutes matching one of `refresh_schedules`. Off unless asked for."""
    refresh_after_idle: Optional[int] = None
    refresh_schedules: list[str] = []

    @field_validator('refresh_after_idle', mode='before')
    @classmethod
    def _idle(cls, v):
        if v is None or v == '' or v == 0:
            return None
        return _clamp(v, REFRESH_MIN_SECONDS, REFRESH_MAX_SECONDS)

    @field_validator('refresh_schedules', mode='before')
    @classmethod
    def _refresh_schedules(cls, v):
        if v is None:
            return []
        if not isinstance(v, (list, tuple)) or len(v) > MAX_SCHEDULES:
            raise ValueError(f'At most {MAX_SCHEDULES} schedules are allowed')
        seen = []
        for expression in v:
            expression = normalize_schedule(expression)
            if expression not in seen:
                seen.append(expression)
        return seen


class ViewerSettings(BaseModel, validate_assignment=True):
    # Whether the viewer displays assets served over https with an invalid
    # certificate (self-signed, expired, wrong hostname). Off by default:
    # with it on, anything able to intercept the connection can decide what
    # ends up on the screen. Read by unitotem_viewer.app.WebviewApp at CEF startup,
    # so a change only takes effect on the next viewer start.
    allow_insecure_certs: bool = False

    # Where each window sends its sound: "<viewer id>|<screen name>" -> output description, absent for the default.
    # Keyed on the screen like the playlist assignments, so it survives unplugging and replugging a monitor.
    audio_outputs: dict[str, str] = {}

    # The on-screen keyboard of each window, keyed like the audio outputs
    keyboards: dict[str, KeyboardSettings] = {}

    # The orientation (quarter turns, 0..3) and flip (0 none, 1 horizontal, 2 vertical) of the CONTENT of each window, keyed like the
    # audio outputs: a vertical panel must come back vertical after a reboot. Older versions ignore these keys.
    orientations: dict[str, int] = {}
    flips: dict[str, int] = {}

    @field_validator('orientations', mode='before')
    @classmethod
    def _orientations(cls, v):
        return {str(k): int(n) % 4 for k, n in (v or {}).items() if isinstance(n, int) and not isinstance(n, bool)}

    @field_validator('flips', mode='before')
    @classmethod
    def _flips(cls, v):
        return {str(k): int(n) for k, n in (v or {}).items() if n in (0, 1, 2) and not isinstance(n, bool)}

    # Touch panels, keyed like the audio outputs: the screensaver over a home page, and the refresh of a plain page
    screensavers: dict[str, ScreensaverSettings] = {}
    refreshes: dict[str, RefreshSettings] = {}

    @staticmethod
    def _filepath() -> Path:
        """Beside the playlists, i.e. in the configuration directory."""
        return Path(cmdargs.assets_file).parent / 'viewer_settings.json'

    @classmethod
    def load(cls) -> 'ViewerSettings':
        """Read the stored settings, falling back to the defaults for a node
        that has never had any saved (the normal case on first boot)."""
        path = cls._filepath()
        try:
            with open(path) as file:
                return cls(**json.load(file))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.error('Cannot read viewer settings from {} ({}), using defaults', path, exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump(self.model_dump(), file, indent=4)
            tmp.replace(path)   # atomic: a power cut cannot leave a half file
        except OSError as exc:
            logger.error('Cannot save viewer settings to {}: {}', path, exc)


viewer_settings = ViewerSettings.load()
