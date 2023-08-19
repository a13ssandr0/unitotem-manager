__all__ = [
    "Asset",
    "CFG_FILE",
    "Config",
    "FileInfo",
    "get_dominant_color",
    "get_file_info",
    "human_readable_size",
    "UploadManager",
    "validate_date"
]



import asyncio
from asyncio import iscoroutinefunction
from datetime import datetime
from math import ceil, inf
from os import remove
from os.path import basename, getsize, isfile, join
from pathlib import Path
from shutil import disk_usage
from time import time
from typing import Annotated, Callable, Coroutine, Optional, Union
from uuid import uuid4

from aiofiles import open as aopen
from PIL import Image
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, PrivateAttr,
                      field_serializer, field_validator, model_validator)
from pymediainfo import MediaInfo
from watchdog.events import FileSystemEventHandler
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from .async_timer import Timer

CFG_FILE = Path('/etc/unitotem/unitotem.conf')

_buf_size = 64 * 1024 * 1024 #64MB buffer
_def_dur = 30


TIMERS:dict[str,dict[str,Timer]] = {}

def validate_date(v):
    if isinstance(v, str):
        if not v:
            return None
        return datetime.strptime(v, '%Y-%m-%dT%H:%M')
    return v

class Asset(BaseModel):
    name:str = ''
    url:str
    duration:Union[int,float] = Field(default_factory=lambda: Config.def_duration if 'Config' in globals() else _def_dur, ge=0)
    enabled:bool = False
    uuid:str = Field(default_factory=lambda: uuid4().__str__(), frozen=True)

    ena_date:Annotated[Optional[datetime], BeforeValidator(validate_date)] = None
    _ena_date_old:Optional[datetime] = PrivateAttr(None)

    dis_date:Annotated[Optional[datetime], BeforeValidator(validate_date)] = None
    _dis_date_old:Optional[datetime] = PrivateAttr(None)


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
            #prevent undesired behaviours if both ena_date and dis_date
            #happened before initialization (i.e. while UniTotem was powered off)
            #we check which one should have been last
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

    # @validator('url')

    @field_validator('duration')
    @classmethod
    def duration_default(cls, v, info):
        if 'Config' in globals() and info.data.get('uuid') == Config.assets.current.uuid:
            # Config.assets.next_change_time += (v or inf) - Config.assets.current.duration
            # delta = Config.assets.next_change_time - time()
            delta = (v or inf) - (time() - Config.assets._last_time)
            if delta>0:
                Config.assets._waiting_timer.set_timeout(delta)
            else:
                Config.assets.next_a()
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
            #during __init__, validator gets called before timers are initialized
            #and added to their dictionary, we don't need them yet
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
        return bool(self.enabled)
    
    def __add__(self, other):
        if isinstance(other, Asset):
            other = other.enabled
        return self.enabled + other

    def __radd__(self, other):
        return self.__add__(other)

    def __eq__(self, __value) -> bool:
        try:
            return self.uuid == __value.uuid
        except:
            return False

    class Config:
        validate_assignment = True




class AssetsList(list[Asset]): #, Iterator[Asset]):
    __current: int = -1
    _last_time = 0
    _callback = None
    _loop = None
    _no_assets = Asset(url='https://localhost/unitotem-no-assets', duration=0)
    _first_boot = Asset(url='https://localhost/unitotem-first-boot', duration=0)
    _waiting_evt = asyncio.Event()
    _waiting_timer = Timer(None, None)

    def __init__(self, iterable = []):
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
        
    def find(self, url:str|None = None):
        return list(filter(lambda a: a.url==url, self))

    def insert(self, index, item):
        super().insert(index, Asset.model_validate(item))
        self.callback()

    def append(self, item):
        super().append(Asset.model_validate(item))
        self.callback()

    def extend(self, other):
        super().extend([Asset.model_validate(item) for item in other])
        self.callback()

    def pop(self, index):
        #see __delitem__ for explanation
        e = super().pop(index)

        if e.uuid == self.current.uuid:
            self.next_a()
        
        self.callback()
        
        return e

    def remove(self, value):
        #see __delitem__ for explanation        
        super().remove(value)
        
        if value.uuid == self.current.uuid:
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
        
        #save the uuid of the asset to remove
        uuid = self[_id].uuid

        #remove the asset
        super().__delitem__(_id)
        
        #NOW force asset change to avoid race conditions if the only enabled
        #asset is the one we want to remove and the main controller
        #is changing asset in this exact moment
        if uuid == self.current.uuid:
            self._nct = 0
        
        self.callback()

    def move(self, from_i:int, to_i:int):
        super().insert(to_i, super().pop(from_i))
        if self._current in [from_i, to_i]:
            self.goto_a(None)
        self.callback()

    reset_asset_timer = _waiting_timer.reset

    # def _clamp(self, n): return max(min(super().__len__()-1, n), 0)

    def next_a(self, force = False):
        if not (any(self) or force):
            temp_current = -1
        else:
            temp_current = (self._current + 1) % super().__len__()
            if not (self[temp_current] or force):
                first = next(filter(lambda x: x.enabled, self[temp_current:] + self[:temp_current])) # type: ignore
                temp_current = super().index(first)
        self._current = temp_current

    async def iter_wait(self, *, force = False, waiter = asyncio.Event()):
        self._waiting_timer.cancel()
        self._waiting_timer = Timer(None, self.next_a)
        self.next_a(force=force)
        while not waiter.is_set():
            yield self.current
            await self._waiting_evt.wait()

    def prev_a(self, force = False):
        if not (any(self) or force):
            temp_current = -1
        else:
            temp_current = (self._current - 1) % super().__len__()
            if not (self[temp_current] or force):
                first = next(filter(lambda x: x.enabled, reversed(self[temp_current:] + self[:temp_current]))) # type: ignore
                temp_current = super().index(first)
        self._current = temp_current
        
    def goto_a(self, index:Union[None,int,str] = __current):
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
        return self._first_boot if Config.first_boot else self._no_assets
    
    def set_callback(self, callback: Callable[[list, str|None], Coroutine], loop: asyncio.AbstractEventLoop):
        self._callback = callback
        self._loop = loop

    def callback(self):
        if self._callback != None and self._loop != None:
            asyncio.run_coroutine_threadsafe(self._callback(self.serialize(), 
                    self[self._current].uuid if self._current>=0 else None), self._loop)

    def serialize(self):
        return [a.model_dump(mode='json') for a in self]





#TODO: replace with BaseSettings
class _Config(BaseModel):
    model_config = ConfigDict(
        populate_by_name = True,
        arbitrary_types_allowed=True,
        # json_encoders = {
        #     AssetsList: lambda al: al.model_dump()
        # }
    )
    #TODO: switch from Field assignment to Field annotation
    assets:      AssetsList                = Field(AssetsList(), alias='urls')
    def_duration:int                       = Field(_def_dur, alias='default_duration', ge=0)
    users:       dict[str, dict[str, str]] = {
        'admin': { #default user: name=admin; password=admin (pre-hashed)
            'pass': 'pbkdf2:sha256:260000$Q9SjfHgne5TOB3rb$f2c264b00585135a0c19930ea60e35d45ed862e8c6245d513c45f3f42df51d4c'
        }
    }
    # _current:str = ''
    filename:    Union[str, Path]          = Field('/etc/unitotem/unitotem.conf', exclude=True)
    first_boot:  bool                      = Field(True, exclude=True)

    @field_validator('assets', mode='before')
    @classmethod
    def validate_assets(cls, val):
        return AssetsList(val)
    
    def __call__(self, *, obj=None, filename = filename, first_boot = False):
        if obj == None:
            with open(filename) as o:
                obj = o.read()
        if isinstance(obj, (str,bytes,bytearray)):
            obj = self.model_validate_json(obj)
        else:
            obj = self.model_validate(obj)
        self.assets = AssetsList(obj.assets)
        self.def_duration = obj.def_duration
        self.users = obj.users
        self.first_boot = first_boot


    def save(self, path:Union[str, Path] = CFG_FILE):
        with open(path, 'w') as conf_f:
            conf_f.write(self.model_dump_json(indent=4))
        self.filename = path
        self.first_boot = False


    def reset(self):
        remove(self.filename)
        self(_Config(), first_boot=True) # type: ignore
    
    # def add_user(self, user:str, password:str, administrator:bool = False):
    #     self.users[user] = {'pass': generate_password_hash(password), 'adm': administrator}

    def change_password(self, user:str, password:str):
        self.users[user]['pass'] = generate_password_hash(password)

    def authenticate(self, user:str, password:str):
        return check_password_hash(self.users.get(user, {}).get('pass', ''), password)

    @property
    def enabled_asset_count(self):
        return sum(self.assets)




Config = _Config() # type: ignore






class FileInfo(BaseModel):
    model_config = ConfigDict(validate_assignment=True, frozen=True)
    filename: str
    duration: Optional[str]
    duration_s: int = Config.def_duration
    size: str





class UploadManager(FileSystemEventHandler):

    def __init__(self, folder: Path, scan_callback: Callable[[dict], Coroutine] | None = None, loop: asyncio.AbstractEventLoop | None = None):
        self._folder = folder
        self._files:list[Path] = []
        self._files_info:dict[str, FileInfo] = {}
        self._disk_used = 0
        self._disk_total = disk_usage(folder).total
        self._disk_totalh = human_readable_size(self._disk_total)
        self._callback = scan_callback
        self._evloop = loop

    @property
    def folder(self) -> Path:
        return self._folder

    @property
    def files(self) -> list[Path]:
        return self._files.copy()

    @property
    def filenames(self) -> list[str]:
        return [f.name for f in self._files]

    @property
    def files_info(self) -> dict[str, FileInfo]:
        return self._files_info.copy()

    def serialize(self) -> dict[str, dict]:
        return {k:v.model_dump() for k,v in self._files_info.items()}

    @property
    def disk_used(self) -> int:
        return self._disk_used

    @property
    def disk_usedh(self) -> str:
        return human_readable_size(self._disk_used)

    @property
    def disk_total(self) -> int:
        return self._disk_total
    
    @property
    def disk_totalh(self) -> str:
        return self._disk_totalh

    def scan_folder(self):
        self._files.clear()
        self._files_info.clear()
        for file in self._folder.iterdir():
            if file.is_file():
                self._files.append(file)
                self._files_info[file.name] = get_file_info(file)
        self._disk_used = disk_usage(self._folder).used
        if self._callback != None and self._evloop != None:
            asyncio.run_coroutine_threadsafe(self._callback(self.serialize()), self._evloop)

    def create_filename(self, filename: Union[str, Path, None]):
        if filename == None:
            filename = ''
        
        if isinstance(filename, str):
            filename = Path(filename)

        filename = self._folder.joinpath(
            secure_filename(filename.name) or uuid4().hex[:8]
        )
        # allow files with duplicate filenames, simply add a number at the end
        if filename.exists():
            stem = filename.stem + '_{}'
            i = 1
            while filename.exists():
                i += 1
                filename = filename.with_stem(stem.format(i))
        return filename

    async def save(self, infile, out_filename = None) -> Path:
        if not out_filename:
            if hasattr(infile, 'name'):
                out_filename = infile.name
            elif hasattr(infile, 'filename'):
                out_filename = infile.filename
        
        out_filename = self.create_filename(out_filename)
        try:
            async with aopen(out_filename, 'wb') as out:
                if iscoroutinefunction(infile.read):
                    while buf := await infile.read(_buf_size): 
                        await out.write(buf)
                else:
                    while buf := infile.read(_buf_size):
                        await out.write(buf)
        except FileNotFoundError:
            self.mkdirs() #create directory if not exists
            return await self.save(infile, out_filename)
        
        file_data = get_file_info(out_filename)
        Config.assets.append({
            'name': file_data.filename,
            'url': 'file:' + file_data.filename,
            'duration': file_data.duration_s,
            'enabled': False,
        })
        Config.save()

        return out_filename

    def mkdirs(self):
        self._folder.mkdir(parents=True, exist_ok=True)

    def exists(self, file):
        return self._folder.joinpath(file).exists()

    def remove(self, file):
        for asset in Config.assets.find('file:' + file):
            Config.assets.remove(asset)
        self._folder.joinpath(file).unlink(True)

    def on_closed(self, event):
        super().on_closed(event)
        self.scan_folder()
    
    def on_created(self, event):
        super().on_created(event)
        self.scan_folder()
    
    def on_deleted(self, event):
        super().on_deleted(event)
        self.scan_folder()
    
    def on_moved(self, event):
        super().on_moved(event)
        self.scan_folder()





def human_readable_size(size, decimal_places=2):
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{unit}" # type: ignore


def get_file_info(b, *f, def_dur = Config.def_duration):
    dur = None
    dur_s = def_dur
    if isfile(f := join(b, *f)):
        for track in MediaInfo.parse(f).general_tracks: # type: ignore
            track_data = track.to_data()
            if 'duration' in track_data:
                dur = track_data.get('other_duration', [None])[0]
                dur_s = ceil(int(track_data['duration'])/1000)
                break
    return FileInfo(filename=basename(f), duration=dur, duration_s=dur_s,
        size=human_readable_size(getsize(f)))


def get_dominant_color(pil_img:Image.Image, palette_size=16): # https://stackoverflow.com/a/61730849/9655651
    # Resize image to speed up processing
    img = pil_img.copy()
    img.thumbnail((100, 100))
    # Reduce colors (uses k-means internally)
    paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)
    # Find the color that occurs most often
    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index*3:palette_index*3+3] #type: ignore [reportOptionalSubscript]
    return hex((dominant_color[0]<<16) + (dominant_color[1]<<8) + dominant_color[2])
