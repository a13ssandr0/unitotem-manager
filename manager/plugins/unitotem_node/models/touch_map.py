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
