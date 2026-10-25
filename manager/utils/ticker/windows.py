"""
Where the bar of each window is kept.

INTERIM: the design (docs/layout-and-ticker.md) keeps it inside the window's layout (`WindowLayout.ticker` of
`window_layouts.json`, package A). Until that file exists this is a small `window_tickers.json` beside the playlists,
keyed like the keyboards of viewer_settings (`"<viewer id>|<screen name>"`). Everything else goes through `get`, `put`
and `all_settings`: switching to the layout means rewriting these three functions and nothing else.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from utils.models.ticker import TickerSettings


def _path() -> Path:
    from utils.models.command_line import cmdargs
    return Path(cmdargs.assets_file).parent / 'window_tickers.json'


class WindowTickers:
    def __init__(self, path: Optional[Path] = None):
        self._path_override = path
        self._data: dict[str, TickerSettings] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path_override or _path()

    def load(self):
        self._data = {}
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the ticker settings of the windows from {}: {}', self.path, exc)
            return
        self.replace(raw.get('windows', {}) if isinstance(raw, dict) else {}, save=False)

    def replace(self, raw: dict, save: bool = True):
        self._data = {}
        for key, value in raw.items():
            try:
                self._data[str(key)] = TickerSettings.model_validate(value)
            except ValidationError as exc:
                logger.warning('The ticker settings of {} are not valid and are ignored: {}', key, exc.errors()[0]['msg'])
        if save:
            self.save()

    def get(self, key: str) -> TickerSettings:
        return self._data.get(key) or TickerSettings()

    def put(self, key: str, settings: TickerSettings):
        self._data[key] = settings
        self.save()

    def all_settings(self) -> dict[str, TickerSettings]:
        return dict(self._data)

    def export(self) -> dict:
        return {key: value.model_dump() for key, value in self._data.items()}

    def save(self):
        try:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'version': 1, 'windows': self.export()}, indent=2))
            tmp.replace(self.path)
        except OSError as exc:
            logger.error('Cannot save the ticker settings of the windows to {}: {}', self.path, exc)
