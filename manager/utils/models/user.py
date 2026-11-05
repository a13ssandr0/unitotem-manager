import asyncio
import dataclasses
import json
import time
from collections import namedtuple
from enum import Enum
from typing import Callable, Coroutine, Optional

from loguru import logger
from pydantic import BaseModel, RootModel, field_serializer, field_validator

from utils import osusers, sessions
from utils.models.command_line import cmdargs

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
    """What a user may do in UniTotem, as stored in users.json. The password is the operating system's (PAM, see
    utils/osusers.py): a `password` left by the previous version is ignored and no longer written. Admin is not stored:
    it is the membership of the group sudo, so it is dropped here."""
    permissions: set[UserPerms]
    # Sessions (cookies) issued before this moment are no longer valid, except the one with id `keep_sid` (the session
    # that changed its own password). Kept here so that it survives a restart.
    invalid_before: float = 0.0
    keep_sid: Optional[str] = None

    # noinspection PyNestedDecorators
    @field_validator('permissions', mode='after')
    @classmethod
    def validate_perms(cls, val: set[UserPerms]):
        return {perm for perm in val if perm != UserPerms.admin}

    @field_serializer('permissions')
    def serialize_perms(self, perms: set[UserPerms]):
        return sorted(perm.value for perm in perms)



@dataclasses.dataclass
class Account:
    """An account as the UI shows it: its stored capabilities, plus admin when the system's group sudo says so."""
    name: str
    permissions: set[UserPerms]


@dataclasses.dataclass
class User:
    name: str
    perms: set[UserPerms]
    sid: Optional[str] = None       # the session this object was loaded for (None outside a request)

    @property
    def has_perm(self):
        return UserPerms.namedtuple(**{p.value: (p in self.perms or UserPerms.admin in self.perms) for p in UserPerms})


class UserManager(RootModel):
    root:dict[str, UserData] = dict()
    __callback = None

    def __getitem__(self, username: str) -> UserData:
        if username not in self.root:
            return UserData(permissions=set())
        return self.root.__getitem__(username)

    def __setitem__(self, username: str, value: UserData):
        self.root.__setitem__(username, UserData.model_validate(value))
        self.callback()
        self.save()

    def __len__(self) -> int:
        return len(self.users())

    def __contains__(self, username: str):
        return username in self.users()

    def users(self):
        """Every account that can log in with a capability record: the ones in users.json and the members of the group
        `users` that were added to it by hand (they have no capability until an administrator gives them some). root
        never logs in to the web, so it is never listed."""
        return [*(name for name in self.root if name != 'root' and osusers.exists(name)),
                *(name for name in osusers.members() if name not in self.root)]

    def items(self) -> list[tuple[str, Account]]:
        """Every account with the capabilities the UI shows: the stored ones, and admin when the system says so."""
        return [(name, Account(name, self._perms(name))) for name in self.users()]

    def _perms(self, username: str) -> set[UserPerms]:
        perms = set(self.root[username].permissions) if username in self.root else set()
        if osusers.is_admin(username):
            perms.add(UserPerms.admin)
        return perms

    def add_user(self, username: str, password: str, permissions: set[UserPerms] = None):
        """Creates the system account, in group sudo when admin is one of the permissions (raises osusers.OSUserError),
        and stores the other capabilities."""
        permissions = set(permissions or ())
        osusers.create_user(username, password, admin=UserPerms.admin in permissions)
        self[username] = UserData(permissions=permissions - {UserPerms.admin})

    def get_user(self, username: str):
        """The user, if its account can log in (group `users`, never root): its capabilities, and admin when it is in
        the group sudo. Deleting the account from the system ends its sessions."""
        if not osusers.may_login(username):
            return None
        return User(username, self._perms(username))

    def session_valid(self, username: str, sid: Optional[str], issued: Optional[float]) -> bool:
        """A cookie issued at `issued` for the session `sid`: not valid any more once the account's sessions were revoked
        (password changed), unless it is the session that was kept."""
        data = self.root.get(username)
        if data is None or not data.invalid_before:
            return True
        return (issued or 0.0) >= data.invalid_before or (sid is not None and sid == data.keep_sid)

    def revoke_sessions(self, username: str, keep_sid: Optional[str] = None):
        """End every session of the user, but `keep_sid`: cookies stop working and open sockets are closed."""
        data = self.root.setdefault(username, UserData(permissions=set()))
        data.invalid_before = time.time()
        data.keep_sid = keep_sid
        self.save()
        sessions.close_soon(username, keep_sid)

    def has_admin(self) -> bool:
        """An administrator who can log in exists (root does not count: a node always has root, and this decides whether
        the first-access setup is still to be done)."""
        return osusers.admin_exists()

    def admins(self) -> list[str]:
        """The names of the administrators (members of the group sudo that can log in)."""
        return [name for name, data in self.items() if UserPerms.admin in data.permissions]

    def change_password(self, username: str, password: str):
        osusers.set_password(username, password)

    def change_perms(self, username: str, permissions: set[UserPerms]):
        """Sets the capabilities. Admin is the membership of the group sudo: it is changed on the system first, so a
        refusal (osusers.OSUserError) stores nothing."""
        admin = UserPerms.admin in permissions
        if admin != osusers.is_admin(username):
            osusers.set_admin(username, admin)
        self.root.setdefault(username, UserData(permissions=set())).permissions = permissions - {UserPerms.admin}
        self.callback()
        self.save()

    def delete_user(self, username: str):
        osusers.remove_user(username)
        self.root.pop(username, None)
        self.callback()
        self.save()
        sessions.close_soon(username)

    def authenticate(self, username: str, password: str):
        return osusers.authenticate(username, password)

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
        cmdargs.users_file.chmod(0o600)


user_manager = UserManager()
