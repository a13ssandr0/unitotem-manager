"""
Security settings of the node that are not about one user, persisted next to the users (/etc/unitotem/security.json).

Today: whether the password policy (utils/passwords.py) is ENFORCED when an administrator creates a user or changes a
password. It is opt-in, decided with the user: off by default, a node whose owner wants it switches it on.
"""
import json
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from utils.models.command_line import cmdargs


class SecuritySettings(BaseModel, validate_assignment=True):
    enforce_password_policy: bool = False

    @staticmethod
    def _filepath() -> Path:
        return Path(cmdargs.users_file).parent / 'security.json'

    @classmethod
    def load(cls) -> 'SecuritySettings':
        try:
            with open(cls._filepath()) as file:
                return cls(**json.load(file))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the security settings ({}), using defaults', exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump(self.model_dump(), file, indent=4)
            tmp.replace(path)
        except OSError as exc:
            logger.error('Cannot save the security settings to {}: {}', path, exc)


security_settings = SecuritySettings.load()
