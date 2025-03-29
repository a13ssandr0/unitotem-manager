import dataclasses
from collections import namedtuple
from enum import Enum

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequiresMeta(type):
    def __getattr__(cls, name):
        try:
            logger.trace(f'UserPerms.requires: {UserPerms[name].value}')
            if name == 'admin':
                logger.debug('Explicitly setting admin permission is redundant as it is assumed by default')
        except KeyError:
            if name != 'none':
                raise KeyError(f'Permission "{name}" does not exist in {UserPerms.__name__}')

        def set_perm(func):
            logger.trace(f'Adding permission {name} to {func.__name__}')
            if hasattr(func, 'perms') and isinstance(func.perms, set):
                if name!="none":
                    func.perms.add(UserPerms[name])
                    # noinspection PyTypeChecker
                    logger.debug(f'{func.__name__} requires {' or '.join(func.perms)} permission to be executed')
                else:
                    logger.debug('{func.__name__} already has stricter permissions, ignoring "none"')
            elif name != "none":
                func.perms = {UserPerms[name]}
                logger.debug(f'{func.__name__} requires {name} permission to be executed')
            else:
                func.perms = None
                logger.debug(f'{func.__name__} requires no permission to be executed')
            return func
        return set_perm


class UserPerms(str, Enum):
    scheduler = "scheduler"
    power = "power"
    audio = "audio"
    admin = "admin"

    # noinspection PyPep8Naming
    @staticmethod
    class requires(metaclass=RequiresMeta):
        pass

    @classmethod
    def namedtuple(cls, *args, **kwargs):
        return namedtuple(cls.__name__, [e.value for e in cls], defaults=[False for _ in cls])(*args, **kwargs)


class UserData(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)
    password: str = Field(validation_alias='pass')
    perms: set[UserPerms]

    # noinspection PyNestedDecorators
    @field_validator('perms', mode='after')
    @classmethod
    def validate_perms(cls, val: set[UserPerms]):
        if UserPerms.admin in val:
            return {UserPerms.admin}
        else:
            return val


@dataclasses.dataclass
class User:
    name: str
    perms: set[UserPerms]

    @property
    def has_perm(self):
        return UserPerms.namedtuple(**{p.value:(p in self.perms or UserPerms.admin in self.perms) for p in UserPerms})
