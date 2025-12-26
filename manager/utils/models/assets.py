from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from enum import IntEnum
from math import inf
from os import environ as env
from time import time
from typing import Any, Callable, Coroutine, Optional, Union
from urllib.parse import urlsplit

from benedict.dicts.parse.parse_util import parse_datetime
from loguru import logger
from pydantic import BaseModel, Field, field_serializer, field_validator, \
    model_validator
from pydantic_core.core_schema import ValidationInfo
from pydantic_extra_types.color import Color

from api.commons import SHUTDOWN_EVENT
from utils import constants as const
from utils.async_timer import Timer
from utils.environment import environ
from utils.extras import strtobool
from utils.models.command_line import cmdargs

TIMERS: dict[str, dict[str, Timer]] = {}


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


class Asset(BaseModel, validate_assignment=True):
    name: str = ''
    url: str
    # dynamically gets default duration value, allowing assets manager to change the default value
    duration: int = Field(default_factory=lambda: const.def_duration, ge=0)
    enabled: bool = False
    fit: FitEnum = FitEnum.contain
    bg_color: Optional[Color] = None
    media_type: MediaType = MediaType.undefined
    uuid: str = Field(default_factory=lambda: os.urandom(16).hex(), frozen=True)

    ena_date: Optional[datetime] = None
    __ena_date_old: Optional[datetime] = None

    dis_date: Optional[datetime] = None
    __dis_date_old: Optional[datetime] = None

    __assets_manager: Optional[AssetsManager] = None

    def __enable(self):
        self.__ena_date_old = None
        self.ena_date = None
        self.enable()
        if self.__assets_manager:
            self.__assets_manager.callback()
            self.__assets_manager.save()

    def __disable(self):
        self.__dis_date_old = None
        self.dis_date = None
        self.disable()
        if self.__assets_manager:
            self.__assets_manager.callback()
            self.__assets_manager.save()

    # noinspection PyNestedDecorators
    @model_validator(mode='before')
    @classmethod
    def validator_wrapper(cls, data: Any) -> Any:
        __ena_date = parse_datetime(data['ena_date']) if 'ena_date' in data else None
        __dis_date = parse_datetime(data['dis_date']) if 'dis_date' in data else None
        __now = datetime.now()

        if __ena_date and __dis_date and __ena_date <= __now and __dis_date <= __now:
            data['enabled'] = __ena_date >= __dis_date
            data['ena_date'] = None
            data['dis_date'] = None
        elif __ena_date and __ena_date <= __now:
            data['enabled'] = True
            data['ena_date'] = None
        elif __dis_date and __dis_date <= __now:
            data['enabled'] = False
            data['dis_date'] = None

        return data

    def model_post_init(self, context: Any, /) -> None:
        self.__ena_date_old = self.ena_date
        self.__dis_date_old = self.dis_date

        TIMERS[self.uuid] = {
            'ena': Timer(self.ena_date, self.__enable),
            'dis': Timer(self.dis_date, self.__disable)
        }

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
                # noinspection HttpUrlsUsage
                scheme = 'http://'
        return scheme + v

    def update_duration(self, duration):
        self.duration = duration
        if self.__assets_manager:
            self.__assets_manager.update_timer(self.uuid)

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

    # noinspection PyNestedDecorators
    @field_validator('ena_date', 'dis_date')
    @classmethod
    def validate_date(cls, v: Optional[datetime], info: ValidationInfo) -> Optional[datetime]:
        if info.data['uuid'] in TIMERS:
            # during __init__, validator gets called before timers are initialized
            # and added to their dictionary, we don't need them yet
            TIMERS[info.data['uuid']][info.field_name.removesuffix('_date')].set_timeout(v)
        return v

    @field_serializer('bg_color')
    def serialize_color(self, c: Color):
        if c is None:
            return None
        return c.as_hex('long')

    def __del__(self):
        if self.uuid in TIMERS:
            TIMERS[self.uuid]['ena'].cancel()
            TIMERS[self.uuid]['dis'].cancel()
            del TIMERS[self.uuid]

    def __bool__(self):
        return self.enabled

    #needed for easy calculation of total enabled asset count DO NOT REMOVE
    def __add__(self, other):
        if isinstance(other, self.__class__):
            other = other.enabled
        return self.enabled + other

    def __radd__(self, other):
        return self.__add__(other)
    
    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.uuid == other.uuid
        else:
            return False


no_assets = Asset(url='https://localhost/unitotem-no-assets', duration=0, media_type=MediaType.web)
first_boot = Asset(url='https://localhost/unitotem-first-boot', duration=0, media_type=MediaType.web)


class AssetsManager(BaseModel, validate_assignment=True):
    # default_duration MUST be validated before assets so that assets without
    # duration attribute set can get the correct value
    default_duration: int = const.def_duration
    assets: list[Asset] = Field(default_factory=list)
    __current = -1
    __last_change_time = 0
    __callback = None
    __waiting_evt = asyncio.Event()
    __waiting_timer = None

    # noinspection PyNestedDecorators
    @field_validator('default_duration')
    @classmethod
    def after_asset_validation(cls, v:int):
        const.def_duration = v
        return v

    def model_post_init(self, context: Any, /) -> None:
        # after initialization set the timer callback function to
        # avoid assigning later or adding overheads with if statements
        self.__waiting_timer = Timer(None, self.next_a)

    def __getitem__(self, item):
        if isinstance(item, str):
            for asset in self.assets:
                if asset.uuid == item:
                    return asset
            else:
                raise ValueError(f'No asset with uuid {item} in list')
        else:
            return self.assets[item]

    def __setitem__(self, index, item):
        self.assets[index] = Asset.model_validate(item)
        self.callback()
        self.save()

    def append(self, __object):
        # Asset.model_validate(asset) is asset = True
        self.assets.append(Asset.model_validate(__object))
        self.callback()
        self.save()

    def extend(self, __iterable):
        # not the most efficient way to do it, but if it's ever going to be used
        # no realistic number of objects will ever be a performance issue
        for __object in __iterable:
            self.assets.append(Asset.model_validate(__object))
        self.callback()
        self.save()

    def insert(self, __index, __object):
        self.assets.insert(__index, Asset.model_validate(__object))
        self.callback()
        self.save()

    def pop(self, __index=-1):
        # see __delitem__ for explanation
        curr_uuid = self.current.uuid
        if __index <= self.__current: self.__current -= 1
        e = self.assets.pop(__index)

        if e.uuid == curr_uuid:
            self.next_a()

        self.callback()
        self.save()

        return e

    def remove(self, __value):
        # see __delitem__ for explanation
        curr_uuid = self.current.uuid
        if self.assets.index(__value) <= self.__current: self.__current -= 1
        self.assets.remove(__value)

        if __value.uuid == curr_uuid:
            self.next_a()

        self.callback()
        self.save()

    def __delitem__(self, __key):
        if isinstance(__key, str):
            uuid = __key
            try:
                # noinspection PyArgumentList
                __key = self.assets.index(Asset(url='', uuid=uuid))
            except ValueError:
                return
        else:
            uuid = self.assets[__key].uuid

        # save the uuid of the asset to remove
        curr_uuid = self.current.uuid
        if __key <= self.__current: self.__current -= 1
        # remove the asset
        self.assets.__delitem__(__key)

        # NOW force asset change to avoid race conditions if the only enabled
        # asset is the one we want to remove and the main controller
        # is changing asset in this exact moment
        if uuid == curr_uuid:
            self.next_a()

        self.callback()
        self.save()

    # def sort(self, *, key:Callable=None, reverse:bool=False):
    #     self.assets.sort(key=key, reverse=reverse)
    #     self.callback()
    #     self.save()

    def index(self, __value, __start=0, __stop=sys.maxsize):
        """
        Return first index of asset by uuid
        """
        # the C implementation of list.index iterates all the elements of self and
        # compares them with __value using the __eq__ method, 
        # so it's only necessary to create a temporary asset with the uuid 
        # contained in __value and use it to make the comparison
        if isinstance(__value, str):
            # noinspection PyArgumentList
            __value = Asset(url='', uuid=__value)
        return self.assets.index(__value, __start, __stop)

    def find(self, url: str):
        """
        Find by url
        """
        return list(filter(lambda a: a.url == url, self.assets))

    def move(self, __old: int, __new: int):
        self.assets.insert(__new, self.assets.pop(__old))
        self.callback()
        if self.__current in [__old, __new]:
            self.goto_a(None)
        self.save()

    def next_a(self, force=False):
        if not force and not self.has_enabled():
            temp_current = -1
        else:
            temp_current = (self.__current + 1) % self.assets.__len__()
            if not force and not self.assets[temp_current].enabled:
                first = next(filter(lambda x: x.enabled, self.assets[temp_current:] + self.assets[:temp_current]))
                temp_current = self.assets.index(first)
        self.__set_current(temp_current)

    def prev_a(self, force=False):
        if not force and not self.has_enabled():
            temp_current = -1
        else:
            temp_current = (self.__current - 1) % self.assets.__len__()
            if not force and not self.assets[temp_current].enabled:
                first = next(filter(lambda x: x.enabled, reversed(self.assets[temp_current:] + self.assets[:temp_current])))
                temp_current = self.assets.index(first)
        self.__set_current(temp_current)

    def goto_a(self, index: Union[None, int, str] = None):
        if index is None:
            temp_current = self.__current
        elif isinstance(index, str):
            # noinspection PyArgumentList
            temp_current = self.assets.index(Asset(url='', uuid=index))
        else:
            temp_current = index % self.assets.__len__() 
        self.__set_current(temp_current)

    @property
    def current(self):
        if 0 <= self.__current < self.assets.__len__():
            return self.assets[self.__current]
        return first_boot if environ._unitotem_first_boot else no_assets

    def __set_current(self, value):
        self.__current = value
        self.__last_change_time = time()
        self.__waiting_evt.set()
        self.__waiting_evt.clear()
        self.__waiting_timer.set_timeout(self.current.duration or inf)

    def count_enabled(self):
        """Count enabled assets"""
        return sum(self.assets)

    def has_enabled(self):
        return any(self.assets)

    def __len__(self):
        return self.assets.__len__()

    def __contains__(self, __key):
        return self.assets.__contains__(__key)

    def __reversed__(self):
        return self.assets.__reversed__()

    def clear(self):
        self.assets.clear()
        self.callback()
        self.save()

    # def reverse(self):
    #     self.assets.reverse()
    #     self.callback()

    def __iter__(self):
        return iter(self.assets)

    async def iter_wait(self):
        self.__waiting_timer.cancel()
        # bootstrap
        self.next_a()
        while not SHUTDOWN_EVENT.is_set():
            yield self.current
            await self.__waiting_evt.wait()

    def update_timer(self, uuid):
        """Call this function each time the duration of an asset is changed"""
        if uuid == self.current.uuid:
            delta = (self.current.duration or inf) - (time() - self.__last_change_time)
            if delta > 0:
                self.__waiting_timer.set_timeout(delta)
            else:
                self.next_a()

    def set_callback(self, callback: Callable[[list, str | None], Coroutine]):
        self.__callback = callback

    def callback(self):
        if self.__callback is not None:
            asyncio.get_event_loop().create_task(
                    self.__callback(
                            self.model_dump()['assets'],
                            self.assets[self.__current].uuid if self.__current >= 0 else None
                    ))

    def serialize_assets(self):
        return self.model_dump()['assets']

    def load(self):
        logger.info('Loading assets from {}', cmdargs.assets_file)
        with open(cmdargs.assets_file) as file:
            self.__init__(**json.load(file))
            logger.success('Found {} assets', len(self.assets))

    def save(self):
        self.callback()
        logger.info('Saving assets in {}', cmdargs.assets_file)
        with open(cmdargs.assets_file, 'w') as file:
            json.dump(self.model_dump(), file, indent=4)


assets_manager = AssetsManager()
