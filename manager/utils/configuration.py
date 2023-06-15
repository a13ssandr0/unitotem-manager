__all__ = [
    "CFG_FILE",
    "Asset",
    "Config",
]



from os import remove
from pathlib import Path
from typing import Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, Protocol, StrBytes, validator
from werkzeug.security import check_password_hash, generate_password_hash



CFG_FILE = Path('/etc/unitotem/unitotem.conf')

_def_dur = 30


class Asset(BaseModel):
    url:str
    duration:Optional[int] = Field(_def_dur, ge=0)
    enabled:Optional[bool] = False
    uuid:Optional[str] = Field(str(uuid4()), allow_mutation=False)

    # @validator('url')

    @validator('duration')
    def duration_default(cls, v):
        return _def_dur if v == None else v

    @validator('enabled')
    def enabled_default(cls, v):
        return v or False

    @validator('uuid')
    def uuid_default(cls, v):
        return v or str(uuid4())

    def __bool__(self):
        return self.enabled

    class Config:
        validate_assignment = True



class _Config(BaseModel):
    assets:list[Asset] = Field([], alias='urls')
    def_duration:int = Field(_def_dur, alias='default_duration', ge=0)
    users:dict[str, dict[str, str]] = {'admin': {'pass': 'pbkdf2:sha256:260000$Q9SjfHgne5TOB3rb$f2c264b00585135a0c19930ea60e35d45ed862e8c6245d513c45f3f42df51d4c'}} #default user: name=admin; password=admin (pre-hashed)
    # _current:str = ''
    filename:Union[str, Path] = Field('/etc/unitotem/unitotem.conf', exclude=True)
    first_boot:bool = Field(True, exclude=True)
    
    def self_load(self, obj, first_boot = False):
        self.assets = obj.assets
        self.def_duration = obj.def_duration
        self.users = obj.users
        self.first_boot = first_boot

    def parse_obj_(self, obj):
        self.self_load(self.parse_obj(obj))

    def parse_raw_(self,
                b:StrBytes,
                *,
                content_type:str = None, # type: ignore
                encoding:str = 'utf8',
                proto:Protocol = None, # type: ignore
                allow_pickle:bool = False):
        
        self.self_load(self.parse_raw(b,
                    content_type=content_type,
                    encoding=encoding,
                    proto=proto,
                    allow_pickle=allow_pickle))
        

    def parse_file_(self,
                path:Union[str, Path] = CFG_FILE,
                *,
                content_type:str = None, # type: ignore
                encoding:str = 'utf8',
                proto:Protocol = None, # type: ignore
                allow_pickle:bool = False):
        
        self.self_load(self.parse_file(path,
                    content_type=content_type,
                    encoding=encoding,
                    proto=proto,
                    allow_pickle=allow_pickle))
        self.filename = path


    def save(self, path:Union[str, Path] = CFG_FILE):
        with open(path, 'w') as conf_f:
            conf_f.write(self.json(indent=4))
        self.filename = path
        self.first_boot = False


    def reset(self):
        remove(self.filename)
        self.self_load(_Config(), True) # type: ignore

    # def cycle_enabled(self):
    #     for asset in cycle(self.assets):
    #         if asset:
    #             self._current = asset.uuid
    #             yield asset

    def assets_json(self):
        return self.dict()['assets']

    def add_asset(self, url:str, duration=None, enabled=None, uuid=None):
        self.assets.append(Asset(url=url, duration=duration, enabled=enabled, uuid=uuid))

    def get_asset(self, uuid: str) -> tuple[int, Asset]:
        for index, asset in enumerate(self.assets):
            if asset.uuid == uuid:
                return index, asset
        else:
            raise ValueError(f'No asset with uuid {uuid} in list')

    def find_assets(self, url:str):
        return filter(lambda a: a[1].url==url, enumerate(self.assets))

    def move_asset(self, from_i:int, to_i:int):
        self.assets.insert(to_i, self.assets.pop(from_i))

    def remove_asset(self, asset:Asset|str):
        if isinstance(asset, str):
            asset = self.get_asset(asset)[1]
        self.assets.remove(asset)

    # def add_user(self, user:str, password:str, administrator:bool = False):
    #     self.users[user] = {'pass': generate_password_hash(password), 'adm': administrator}

    def change_password(self, user:str, password:str):
        self.users[user]['pass'] = generate_password_hash(password)

    def authenticate(self, user:str, password:str):
        return check_password_hash(self.users.get(user, {}).get('pass', ''), password)

    @property
    def enabled_asset_count(self):
        return sum(1 for a in self.assets if a.enabled)

    # @property
    # def current_asset(self):
    #     return self._current

    class Config:
        allow_population_by_field_name = True


Config = _Config() # type: ignore