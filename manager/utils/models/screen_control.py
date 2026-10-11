"""
Settings of the screen power control, persisted next to the playlists (/etc/unitotem/screen_control.json).

Per output: which mechanism to prefer, the configuration of the ones that cannot be probed (Wake-on-LAN, serial) and
what the last probe found (DDC bus, advertised input sources, CEC adapter), so the slow probe runs once per
monitor and not at every start. Atomic writes, like every other file in that directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from utils.models.command_line import cmdargs

MechanismChoice = Literal['auto', 'ddc', 'cec', 'x', 'wol', 'serial']
OUTPUT_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


class WolConfig(BaseModel, validate_assignment=True):
    mac: str
    broadcast: str = '255.255.255.255'
    port: int = Field(default=9, ge=1, le=65535)

    @field_validator('mac')
    @classmethod
    def _mac(cls, v: str) -> str:
        from utils.system.screens.wol import normalise_mac
        return normalise_mac(v)


class SerialConfig(BaseModel, validate_assignment=True):
    port: str
    baud: int = 9600
    on_command: str = ''
    off_command: str = ''

    @field_validator('port')
    @classmethod
    def _port(cls, v: str) -> str:
        from utils.system.screens.serial import valid_port
        if not valid_port(v):
            raise ValueError(f'{v!r} is not a serial port')
        return v

    @field_validator('on_command', 'off_command')
    @classmethod
    def _command(cls, v: str) -> str:
        from utils.system.screens.serial import parse_command
        parse_command(v)  # raises on anything that is not ASCII
        return v


class OutputConfig(BaseModel, validate_assignment=True):
    on_mechanism: MechanismChoice = 'auto'
    off_mechanism: MechanismChoice = 'auto'
    # Switch the screen on when the manager starts: a monitor that is off after a boot (or a power cut) is the case
    # this exists for, and the new window appears through the usual hot-plug path once it is on
    power_on_at_start: bool = True
    wol: Optional[WolConfig] = None
    serial: Optional[SerialConfig] = None
    cec_device: Optional[str] = None

    # Cached probe results
    identity: Optional[str] = None
    ddc_bus: Optional[int] = None
    ddc_capabilities: dict[str, dict[str, str]] = Field(default_factory=dict)


class ScreenControl(BaseModel, validate_assignment=True):
    outputs: dict[str, OutputConfig] = Field(default_factory=dict)

    def output(self, name: str) -> OutputConfig:
        if not OUTPUT_NAME_RE.match(name):
            raise ValueError(f'{name!r} is not an output name')
        return self.outputs.setdefault(name, OutputConfig())

    @staticmethod
    def _filepath() -> Path:
        return Path(cmdargs.assets_file).parent / 'screen_control.json'

    @classmethod
    def load(cls) -> 'ScreenControl':
        path = cls._filepath()
        try:
            with open(path) as file:
                return cls(**json.load(file))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the screen control settings from {} ({}), using defaults', path, exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump(self.model_dump(), file, indent=4)
                file.flush()
                import os
                os.fsync(file.fileno())
            tmp.replace(path)
        except OSError as exc:
            logger.error('Cannot save the screen control settings to {}: {}', path, exc)


screen_control = ScreenControl.load()
