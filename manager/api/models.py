__all__ = [
    "Asset",
    "Config",
    "FitEnum",
    "MediaType",
    "validate_date"
]

import asyncio
import os
from datetime import datetime
from enum import IntEnum
from ipaddress import IPv4Address
from math import inf
from os import environ, environ as env, remove
from pathlib import Path
from time import time
from typing import Annotated, Callable, Coroutine, Optional, Union
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv, set_key
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field,
                      PositiveInt, PrivateAttr, field_serializer,
                      field_validator, model_validator)
from pydantic_extra_types.color import Color
from werkzeug.security import check_password_hash, generate_password_hash

from api import constants as const
from utils.async_timer import Timer
from utils.extras import strtobool
from utils.models.user import User, UserData, UserPerms

load_dotenv(const.envfile)

if 'instance_id' not in environ:
    environ['instance_id'] = os.urandom(16).hex()
    const.envfile.touch(mode=0o600)
    set_key(const.envfile, 'instance_id', environ['instance_id'])

TIMERS: dict[str, dict[str, Timer]] = {}


def validate_date(v):
    if isinstance(v, str):
        if not v:
            return None
        return datetime.strptime(v, '%Y-%m-%dT%H:%M')
    return v


class FitEnum(IntEnum):
    contain = 0
    cover = 1
    fill = 2


class MediaType(IntEnum):
    undefined = -1
    web = 0
    image = 1
    video = 2
    audio = 3


class Asset(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    name: str = ''
    url: str
    duration: Union[int, float] = Field(
        default_factory=lambda: Config.def_duration if 'Config' in globals() else const.def_duration, ge=0)
    enabled: bool = False
    fit: FitEnum = FitEnum.contain
    bg_color: Optional[Color] = None
    media_type: MediaType = MediaType.undefined
    uuid: str = Field(default_factory=lambda: os.urandom(16).hex(), frozen=True)

    ena_date: Annotated[Optional[datetime], BeforeValidator(validate_date)] = None
    _ena_date_old: Optional[datetime] = PrivateAttr(None)

    dis_date: Annotated[Optional[datetime], BeforeValidator(validate_date)] = None
    _dis_date_old: Optional[datetime] = PrivateAttr(None)

    def __enable(self):
        self._ena_date_old = None
        self.ena_date = None
        self.enable()
        Config.save()

    def __disable(self):
        self._dis_date_old = None
        self.dis_date = None
        self.disable()
        Config.save()

    def __init__(self, **data):
        super().__init__(**data)

        TIMERS[self.uuid] = {
            'ena': Timer(None, self.__enable),
            'dis': Timer(None, self.__disable)
        }

        self._ena_date_old = self.ena_date
        self._dis_date_old = self.dis_date

        if self.ena_date and self.dis_date \
                and self.ena_date <= datetime.now() and self.dis_date <= datetime.now():
            # prevent undesired behaviours if both ena_date and dis_date
            # happened before initialization (i.e. while UniTotem was powered off)
            # we check which one should have been last
            if self.ena_date >= self.dis_date:
                self.__enable()
            else:
                self.__disable()
        else:
            if self.ena_date:
                if self.ena_date > datetime.now():
                    TIMERS[self.uuid]['ena'].set_timeout(self.ena_date)
                else:
                    self.__enable()

            if self.dis_date:
                if self.dis_date > datetime.now():
                    TIMERS[self.uuid]['dis'].set_timeout(self.dis_date)
                else:
                    self.__disable()

    def __del__(self):
        TIMERS[self.uuid]['ena'].cancel()
        TIMERS[self.uuid]['dis'].cancel()
        del TIMERS[self.uuid]

    # noinspection PyNestedDecorators
    @field_validator('url')
    @classmethod
    def url_guesser(cls, v):
        url = urlsplit(v)
        scheme = ''
        if not url.scheme:
            if (url.netloc or url.path).startswith('ftp.'):
                scheme = 'ftp://'
            else:
                scheme = 'http://'
        return scheme + v

    # noinspection PyNestedDecorators
    @field_validator('duration')
    @classmethod
    def duration_default(cls, v, info):
        if 'Config' in globals() and info.data.get('uuid') == Config.assets.current.uuid:
            delta = (v or inf) - (time() - Config.assets._last_time)
            if delta > 0:
                Config.assets._waiting_timer.set_timeout(delta)
            else:
                Config.assets.next_a()
        return v

    # noinspection PyNestedDecorators
    @field_validator('media_type', mode='before')
    @classmethod
    def mime_validator(cls, v):
        if isinstance(v, str):
            if 'image' in v:
                return MediaType.image
            elif 'video' in v:
                return MediaType.video
            elif 'audio' in v:
                return MediaType.audio
            else:
                return MediaType.undefined
        else:
            return v

    def enable(self):
        start_loop = not Config.enabled_asset_count
        self.enabled = True
        if 'Config' in globals() and start_loop:
            Config.assets.next_a()

    def disable(self):
        self.enabled = False
        if 'Config' in globals() and self.uuid == Config.assets.current.uuid:
            Config.assets.next_a()

    @model_validator(mode='after')
    def run_update(self):
        if self.uuid in TIMERS:
            # during __init__, validator gets called before timers are initialized
            # and added to their dictionary, we don't need them yet
            if self.ena_date != self._ena_date_old:
                self._ena_date_old = self.ena_date
                if self.ena_date is None:
                    TIMERS[self.uuid]['ena'].cancel()
                elif self.ena_date <= datetime.now():
                    TIMERS[self.uuid]['ena'].cancel()
                    self.__enable()
                else:
                    TIMERS[self.uuid]['ena'].set_timeout(self.ena_date)

            if self.dis_date != self._dis_date_old:
                self._dis_date_old = self.dis_date
                if self.dis_date is None:
                    TIMERS[self.uuid]['dis'].cancel()
                elif self.dis_date <= datetime.now():
                    TIMERS[self.uuid]['dis'].cancel()
                    self.__disable()
                else:
                    TIMERS[self.uuid]['dis'].set_timeout(self.dis_date)
        if 'Config' in globals():
            Config.assets.callback()
        return self

    @field_serializer('ena_date', 'dis_date')
    def serialize_date(self, dt: Optional[datetime]):
        if dt is None: return None
        return dt.strftime('%4Y-%m-%dT%H:%M')

    def __bool__(self):
        return self.enabled

    def __eq__(self, __value) -> bool:
        try:
            return self.uuid == __value.uuid
        except:
            return False



class AssetsList(list[Asset]):  # , Iterator[Asset]):
    __current: int = -1
    _last_time = 0
    _callback = None
    _no_assets = Asset(url='https://localhost/unitotem-no-assets', duration=0, media_type=MediaType.web)
    _first_boot = Asset(url='https://localhost/unitotem-first-boot', duration=0, media_type=MediaType.web)
    _waiting_evt = asyncio.Event()
    _waiting_timer = Timer(None, None)

    def __init__(self, iterable=None):
        if iterable is None:
            iterable = []
        super().__init__([Asset.model_validate(e) for e in iterable])
        self.callback()

    def __setitem__(self, index, item):
        super().__setitem__(index, Asset.model_validate(item))
        self.callback()

    def __getitem__(self, _id):
        if isinstance(_id, str):
            for asset in self:
                if asset.uuid == _id:
                    return asset
            else:
                raise ValueError(f'No asset with uuid {_id} in list')
        else:
            return super().__getitem__(_id)

    def find(self, url: str | None = None):
        return list(filter(lambda a: a.url == url, self))

    def insert(self, index, item):
        super().insert(index, Asset.model_validate(item))
        self.callback()

    def append(self, item):
        super().append(Asset.model_validate(item))
        self.callback()

    def extend(self, other):
        super().extend([Asset.model_validate(item) for item in other])
        self.callback()

    def pop(self, index=-1):
        # see __delitem__ for explanation
        curr_uuid = self.current.uuid
        if index <= self.__current: self.__current -= 1
        e = super().pop(index)

        if e.uuid == curr_uuid:
            self.next_a()

        self.callback()

        return e

    def remove(self, value):
        # see __delitem__ for explanation
        curr_uuid = self.current.uuid
        if self.index(value) <= self.__current: self.__current -= 1
        super().remove(value)

        if value.uuid == curr_uuid:
            self.next_a()

        self.callback()

    def __delitem__(self, _id):
        if isinstance(_id, str):
            for index, asset in enumerate(self):
                if asset.uuid == _id:
                    _id = index
                    break
            else:
                return
                # raise ValueError(f'No asset with uuid {_id} in list')

        # save the uuid of the asset to remove
        uuid = self[_id].uuid
        curr_uuid = self.current.uuid
        if _id <= self.__current: self.__current -= 1
        # remove the asset
        super().__delitem__(_id)
        # NOW force asset change to avoid race conditions if the only enabled
        # asset is the one we want to remove and the main controller
        # is changing asset in this exact moment
        if uuid == curr_uuid:
            self.next_a()

        self.callback()

    def move(self, from_i: int, to_i: int):
        super().insert(to_i, super().pop(from_i))
        self.callback()
        if self._current in [from_i, to_i]:
            self.goto_a(None)

    reset_asset_timer = _waiting_timer.reset

    def next_a(self, force=False):
        if not (any(self) or force):
            temp_current = -1
        else:
            temp_current = (self._current + 1) % super().__len__()
            if not (self[temp_current] or force):
                first = next(filter(lambda x: x.enabled, self[temp_current:] + self[:temp_current]))  # type: ignore
                temp_current = super().index(first)
        self._current = temp_current

    async def iter_wait(self, *, force=False, waiter=asyncio.Event()):
        self._waiting_timer.cancel()
        self._waiting_timer = Timer(None, self.next_a)
        self.next_a(force=force)
        while not waiter.is_set():
            yield self.current
            await self._waiting_evt.wait()

    def prev_a(self, force=False):
        if not (any(self) or force):
            temp_current = -1
        else:
            temp_current = (self._current - 1) % super().__len__()
            if not (self[temp_current] or force):
                first = next(
                    filter(lambda x: x.enabled, reversed(self[temp_current:] + self[:temp_current])))  # type: ignore
                temp_current = super().index(first)
        self._current = temp_current

    def goto_a(self, index: Union[None, int, str] = __current):
        if index is None:
            self._current = self._current
            return
        if isinstance(index, str):
            for i, asset in enumerate(self):
                if asset.uuid == index:
                    index = i
                    break
            else:
                raise ValueError(f'No asset with uuid {index} in list')
        self._current = index % super().__len__()

    @property
    def _current(self):
        return self.__current

    @_current.setter
    def _current(self, val):
        self.__current = val
        self._last_time = time()
        self._waiting_evt.set()
        self._waiting_evt.clear()
        self._waiting_timer.set_timeout(self.current.duration or inf)

    @property
    def current(self):
        if 0 <= self._current < super().__len__():
            return self[self._current]
        return self._first_boot if strtobool(env['unitotem_first_boot']) else self._no_assets

    def set_callback(self, callback: Callable[[list, str | None], Coroutine]):
        self._callback = callback

    def callback(self):
        if self._callback is not None:
            asyncio.get_event_loop().create_task(self._callback(self.serialize(),
                                                            self[self._current].uuid if self._current >= 0 else None))

    def serialize(self):
        return [a.model_dump(mode='json') for a in self]


# TODO: replace with BaseSettings
class _Config(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True
    )
    # TODO: switch from Field assignment to Field annotation
    assets: AssetsList = Field(default_factory=AssetsList, alias='urls')
    def_duration: int = Field(const.def_duration, alias='default_duration', ge=0)
    users: dict[str, UserData] = Field(default_factory=lambda: {
        'admin': UserData(  # default user: name=admin; password=admin
            password=generate_password_hash('admin'),
            perms={UserPerms.admin},
        )
    })
    remote_server_ip: Optional[IPv4Address] = None
    remote_server_port: PositiveInt = const.default_port_secure
    remote_server_id: Optional[str] = None
    remote_server_pk: Optional[rsa.RSAPublicKey] = None
    rsa_pk: rsa.RSAPrivateKey = Field(default_factory=lambda: rsa.generate_private_key(65537, 4096))
    remote_clients: dict[str, dict[str, str | int]] = Field(default_factory=dict)
    filename: Union[str, Path] = Field(const.default_config_file, exclude=True)

    # noinspection PyNestedDecorators
    @field_validator('assets', mode='before')
    @classmethod
    def validate_assets(cls, val):
        return AssetsList(val)

    # noinspection PyNestedDecorators
    @field_validator('users', mode='before')
    @classmethod
    def validate_users(cls, val: dict):
        # needed when updating from versions below 3.0 that had only one user
        if len(val) == 1 and 'admin' in val and 'groups' not in val['admin']:
            val['admin']['groups'] = ['admin']

        for k in val.keys():
            val[k] = UserData(**val[k])
        return val

    # noinspection PyNestedDecorators
    @field_validator('rsa_pk', mode='before')
    @classmethod
    def validate_rsa_pk(cls, pk):
        if pk:
            return serialization.load_pem_private_key(pk.encode(), password=None)

    @field_serializer('rsa_pk')
    def rsa_pk_serializer(self, rsa_pk: rsa.RSAPrivateKey):
        return rsa_pk.private_bytes(encoding=serialization.Encoding.PEM,
                                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                                    encryption_algorithm=serialization.NoEncryption()).decode()

    # noinspection PyNestedDecorators
    @field_validator('remote_server_pk', mode='before')
    @classmethod
    def validate_remote_server_pk(cls, pk):
        if pk:
            return serialization.load_pem_public_key(pk.encode())

    @field_serializer('remote_server_pk')
    def remote_server_pk_serializer(self, rsa_pk: rsa.RSAPublicKey):
        if rsa_pk:
            return rsa_pk.public_bytes(encoding=serialization.Encoding.PEM,
                                       format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def __call__(self, *, obj=None, filename=filename):
        if obj is None:
            self.filename = filename
            with open(filename) as o:
                obj = o.read()
        if isinstance(obj, (str, bytes, bytearray)):
            obj = self.model_validate_json(obj)
        else:
            obj = self.model_validate(obj)
        self.assets = AssetsList(obj.assets)
        self.def_duration = obj.def_duration
        self.users = obj.users
        self.remote_server_ip = obj.remote_server_ip
        self.remote_server_port = obj.remote_server_port
        self.remote_server_id = obj.remote_server_id
        self.remote_server_pk = obj.remote_server_pk
        self.rsa_pk = obj.rsa_pk
        self.remote_clients = obj.remote_clients

    def save(self, path: Union[None, str, Path] = None):
        if path is None:
            path = self.filename
        with open(path, 'w') as conf_f:
            conf_f.write(self.model_dump_json(indent=4))
        self.filename = path
        env['unitotem_first_boot'] = 'False'

    def reset(self):
        remove(self.filename)
        env['unitotem_first_boot'] = 'True'
        # noinspection PyArgumentList
        self(obj = _Config())

    def add_user(self, user: str, password: str, perms:set[UserPerms]=None):
        if perms is None:
            perms = {}
        self.users[user] = UserData(password=generate_password_hash(password), perms=perms)

    def get_user(self, name: str):
        if name in self.users:
            return User(name, self.users[name].perms)

    def change_password(self, user: str, password: str):
        self.users[user].password = generate_password_hash(password)

    def authenticate(self, user: str, password: str):
        if user in self.users:
            return check_password_hash(self.users[user].password, password)

    def associate_client(self, pub_key: str, ip: str):
        _id = os.urandom(16).hex()
        self.remote_clients[_id] = {'pk': pub_key, 'ip': ip}
        self.save()
        return _id

    @property
    def enabled_asset_count(self):
        return sum(self.assets)


Config = _Config()  # type: ignore


