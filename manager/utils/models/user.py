import asyncio
import dataclasses
import json
import warnings
from collections import namedtuple
from enum import Enum
from typing import Callable, Coroutine

from loguru import logger
from pydantic import BaseModel, RootModel, field_serializer, field_validator
from pydantic_core.core_schema import SerializerFunctionWrapHandler
from werkzeug.security import check_password_hash, generate_password_hash

from utils.models.command_line import cmdargs

warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings.*\n.*field_name\=\'permissions\'.*",
    category=UserWarning,
)

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
                if name != "none":
                    func.perms.add(UserPerms[name])
                    # noinspection PyTypeChecker
                    logger.debug(f'{func.__name__} requires {" or ".join(func.perms)} permission to be executed')
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


class UserData(BaseModel, validate_assignment=True):
    password: str
    permissions: set[UserPerms]

    # noinspection PyNestedDecorators
    @field_validator('permissions', mode='after')
    @classmethod
    def validate_perms(cls, val: set[UserPerms]):
        if UserPerms.admin in val:
            return {UserPerms.admin}
        else:
            return val

    @field_serializer('permissions', mode='wrap')
    def serialize_perms(self, perms: set[UserPerms], nxt: SerializerFunctionWrapHandler):
        return nxt(list(perms))



@dataclasses.dataclass
class User:
    name: str
    perms: set[UserPerms]

    @property
    def has_perm(self):
        return UserPerms.namedtuple(**{p.value: (p in self.perms or UserPerms.admin in self.perms) for p in UserPerms})


class UserManager(RootModel):
    root:dict[str, UserData] = dict()
    __callback = None

    def __getitem__(self, username: str) -> UserData:
        return self.root.__getitem__(username)

    def __setitem__(self, username: str, value: UserData):
        self.root.__setitem__(username, UserData.model_validate(value))
        self.callback()
        self.save()

    def __delitem__(self, username: str):
        self.root.__delitem__(username)
        self.callback()
        self.save()

    def __len__(self) -> int:
        return self.root.__len__()

    def __contains__(self, username: str):
        return self.root.__contains__(username)

    def users(self):
        return self.root.keys()

    def items(self):
        return self.root.items()

    def add_user(self, username: str, password: str, permissions: set[UserPerms] = None):
        if permissions is None:
            permissions = set()
        self[username] = UserData(password=generate_password_hash(password), permissions=permissions)

    def get_user(self, username: str):
        return User(username, self.root[username].permissions) if username in self.root else None

    def change_password(self, username: str, password: str):
        self.root[username].password = generate_password_hash(password)
        self.callback()
        self.save()

    def change_perms(self, username: str, permissions: set[UserPerms]):
        self.root[username].permissions = permissions
        self.callback()
        self.save()

    def authenticate(self, username: str, password: str):
        return username in self.root and check_password_hash(self.root[username].password, password)

    def set_callback(self, callback: Callable[[dict[str, UserData]], Coroutine]):
        self.__callback = callback

    def callback(self):
        if self.__callback is not None:
            asyncio.get_event_loop().create_task(self.__callback(self.model_dump()))

    def load(self):
        with open(cmdargs.users_file) as f:
            self.__init__(**json.load(f))

    def save(self):
        with open(cmdargs.users_file, 'w') as f:
            json.dump(self.model_dump(), f, indent=4)


user_manager = UserManager()
