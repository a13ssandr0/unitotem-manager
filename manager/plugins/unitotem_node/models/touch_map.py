"""
Which output each touch screen is mapped to, persisted next to the other configuration (/etc/unitotem/touch_map.json).

Keyed by the stable identity of the device (name, vendor:product, serial: see system/touch.py), never by the X device id,
which changes at every boot and hot-plug. The value is the RandR output name, like everywhere else in the screen layer.
Atomic writes, like every other file in that directory.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from utils.models.command_line import cmdargs

OUTPUT_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


class TouchMap(BaseModel, validate_assignment=True):
    mapping: dict[str, str] = Field(default_factory=dict)       # device identity -> output name
    # device identity -> {'turn': 0..3, 'flip': 0..2}: how the touch input is turned/mirrored (same convention as a window's orientation and flip)
    # before it reaches the screen. Only for a panel that is not glued to its screen and has another natural orientation; absent = as the screen
    orientation: dict[str, dict] = Field(default_factory=dict)

    def set_orientation(self, key: str, turn: int, flip: int):
        if not key or len(key) > 512:
            raise ValueError('not a touch device identity')
        if turn not in (0, 1, 2, 3) or flip not in (0, 1, 2) or isinstance(turn, bool) or isinstance(flip, bool):
            raise ValueError('turn is 0-3 quarter turns and flip 0 (none), 1 (left-right) or 2 (top-bottom)')
        orientation = dict(self.orientation)
        if turn == 0 and flip == 0:
            orientation.pop(key, None)
        else:
            orientation[key] = {'turn': turn, 'flip': flip}
        self.orientation = orientation

    def set(self, key: str, output: str | None):
        if not key or len(key) > 512:
            raise ValueError('not a touch device identity')
        mapping = dict(self.mapping)
        if output is None:
            mapping.pop(key, None)
        else:
            if not OUTPUT_NAME_RE.match(output):
                raise ValueError(f'{output!r} is not an output name')
            mapping[key] = output
        self.mapping = mapping

    @staticmethod
    def _filepath() -> Path:
        return Path(cmdargs.assets_file).parent / 'touch_map.json'

    @classmethod
    def load(cls) -> 'TouchMap':
        path = cls._filepath()
        try:
            with open(path) as file:
                return cls(**json.load(file))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the touch mapping from {} ({}), using defaults', path, exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump(self.model_dump(), file, indent=4)
                file.flush()
                os.fsync(file.fileno())
            tmp.replace(path)
        except OSError as exc:
            logger.error('Cannot save the touch mapping to {}: {}', path, exc)


touch_map = TouchMap.load()
