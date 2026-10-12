"""
Reboot, power-off and screen on/off timers, persisted in /etc/unitotem/timers.json and run by UniTotem itself.

There is no crontab involved: a timer is an action *name* (never a command), a five-field schedule (see
utils.cronexpr) and, for the screen actions, which output and mechanism. What an action does is decided in
`unitotem_node.system.timer_actions`, so nothing a user typed is ever executed as a command line.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal, Optional

from loguru import logger
from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator

from utils.models.command_line import cmdargs
from unitotem_node.models.screen_control import MechanismChoice, OUTPUT_NAME_RE

ACTIONS = ('pwr', 'reb')
SCREEN_ACTIONS = ('scr_on', 'scr_off')
Action = Literal['pwr', 'reb', 'scr_on', 'scr_off']
_LEGACY_MARK = 'unitotem:-)'


class Timer(BaseModel, validate_assignment=True):
    uuid: str = Field(default_factory=lambda: os.urandom(16).hex(), pattern=r'^[0-9a-f]{32}$')
    action: Action
    minute: str
    hour: str
    day_of_month: str
    month: str
    day_of_week: str
    enabled: bool = True
    output: Optional[str] = None
    mechanism: MechanismChoice = 'auto'

    @field_validator('minute', 'hour', 'day_of_month', 'month', 'day_of_week', mode='before')
    @classmethod
    def _text(cls, v):
        v = str(v).strip()        # older clients send plain numbers
        if not v or any(c.isspace() for c in v):
            raise ValueError(f'{v!r} is not a schedule field')    # one field must stay one field
        return v

    @model_validator(mode='after')
    def _check(self):
        if not croniter.is_valid(self.expression):
            raise ValueError(f'{self.expression!r} is not a valid schedule')
        if self.action in SCREEN_ACTIONS:
            if not self.output or not OUTPUT_NAME_RE.match(self.output):
                raise ValueError(f'{self.output!r} is not an output name')
        return self

    @property
    def expression(self) -> str:
        return f'{self.minute} {self.hour} {self.day_of_month} {self.month} {self.day_of_week}'

    def matches(self, when) -> bool:
        return croniter.match(self.expression, when)

    def serialize(self) -> dict:
        return {
            'uuid': self.uuid,
            'command': self.action,
            'output': self.output if self.action in SCREEN_ACTIONS else None,
            'mechanism': self.mechanism if self.action in SCREEN_ACTIONS else None,
            'known': True,
            'minute': self.minute,
            'hour': self.hour,
            'day_of_month': self.day_of_month,
            'month': self.month,
            'day_of_week': self.day_of_week,
            'enabled': self.enabled,
        }


class TimerManager(BaseModel):
    timers: dict[str, Timer] = Field(default_factory=dict)

    @staticmethod
    def _filepath() -> Path:
        return Path(cmdargs.assets_file).parent / 'timers.json'

    @classmethod
    def load(cls) -> 'TimerManager':
        path = cls._filepath()
        try:
            with open(path) as file:
                raw = json.load(file)
            manager = cls()
            for item in raw.get('timers', []):
                try:
                    timer = Timer(**item)
                    manager.timers[timer.uuid] = timer
                except ValueError as exc:
                    logger.error('Ignoring an invalid timer ({}): {}', exc, item)
            return manager
        except FileNotFoundError:
            manager = cls()
            manager._import_legacy()
            return manager
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the timers from {} ({}), starting with none', path, exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump({'timers': [t.model_dump() for t in self.timers.values()]}, file, indent=4)
                file.flush()
                os.fsync(file.fileno())
            tmp.replace(path)
        except OSError as exc:
            logger.error('Cannot save the timers to {}: {}', path, exc)

    def add(self, timer: Timer) -> Timer:
        self.timers[timer.uuid] = timer
        self.save()
        return timer

    def remove(self, uuid: str) -> bool:
        found = self.timers.pop(uuid, None) is not None
        if found:
            self.save()
        return found

    def serialize(self) -> list[dict]:
        return [t.serialize() for t in self.timers.values()]

    def due(self, when) -> list[Timer]:
        return [t for t in self.timers.values() if t.enabled and t.matches(when)]

    def _import_legacy(self):
        """Versions up to 3.0.0~dev4 kept the timers as marked lines in root's crontab (and the details of the screen
        ones in screen_control.json). Read them once, so an upgrade keeps the timers. The crontab is left untouched:
        a rollback to that version finds its timers where it left them."""
        try:
            out = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return
        legacy_screens: dict = {}
        try:
            with open(Path(cmdargs.assets_file).parent / 'screen_control.json') as file:
                legacy_screens = json.load(file).get('timers', {})
        except (OSError, ValueError):
            pass
        commands = {'/usr/sbin/poweroff': 'pwr', '/usr/sbin/reboot': 'reb'}
        for line in out.splitlines():
            line = line.strip()
            enabled = not line.startswith('#')
            line = line.lstrip('# ')
            if _LEGACY_MARK not in line:
                continue
            body, _, comment = line.partition('#')
            uuid = comment.strip().removeprefix(_LEGACY_MARK)
            fields = body.split(None, 5)
            if len(fields) != 6 or not re.fullmatch(r'[0-9a-f]{32}', uuid):
                continue
            command, extra = fields[5].strip(), {}
            if command in commands:
                action = commands[command]
            elif command.endswith(f'screenctl.py {uuid}') and uuid in legacy_screens:
                screen = legacy_screens[uuid]
                action = f"scr_{screen.get('state')}"
                extra = {'output': screen.get('output'), 'mechanism': screen.get('mechanism', 'auto')}
            else:
                continue
            try:
                timer = Timer(uuid=uuid, action=action, minute=fields[0], hour=fields[1], day_of_month=fields[2],
                              month=fields[3], day_of_week=fields[4], enabled=enabled, **extra)
            except ValueError as exc:
                logger.warning('Not importing the crontab timer {}: {}', uuid, exc)
                continue
            self.timers[uuid] = timer
        if self.timers:
            logger.info('Imported {} timers from the crontab', len(self.timers))
            self.save()


timer_manager = TimerManager.load()
