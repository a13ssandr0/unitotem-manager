__all__ = [
    "Asset",
    "CFG_FILE",
    "Config",
    "FileInfo",
    "get_dominant_color",
    "get_file_info",
    "human_readable_size",
    "UploadManager"
]



import asyncio
from collections.abc import Iterator
from os import remove
from os.path import basename, getsize, isfile, join
from pathlib import Path
from shutil import disk_usage
from typing import Callable, Coroutine, Optional, Union, cast
from uuid import uuid4

from aiofiles import open as aopen
from PIL import Image
from pydantic import BaseModel, Field, Protocol, StrBytes, validator
from pymediainfo import MediaInfo, Track
from watchdog.events import FileSystemEventHandler
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from manager.utils.files import Asset


CFG_FILE = Path('/etc/unitotem/unitotem.conf')

_buf_size = 64 * 1024 * 1024 #64MB buffer
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
        return bool(self.enabled)

    class Config:
        validate_assignment = True




""" class AssetsList(list[Asset]): #, Iterator[Asset]):
    _current: int = -1
    _next_change_time: float = 0
    _current_duration: float = 0

    # def __init__(self, iterable = []):
    #     super().__init__(iterable)

    # def __setitem__(self, index, item):
    #     super().__setitem__(index, item)

    # def __getitem__(self, index):
    #     if 0 <= index < super().__len__():
    #         super().__getitem__(index)

    # def insert(self, index, item):
    #     super().insert(index, item)

    # def append(self, item):
    #     super().append(item)

    # def extend(self, other):
    #     if isinstance(other, type(self)):
    #         super().extend(other)
    #     else:
    #         super().extend(item for item in other)

    # def __iter__(self) -> Iterator[Asset]:
    #     return self
    
    # def __next__(self) -> Asset|None:
    #     return self.next()

    def next(self, force = False) -> Asset|None:
        if not (any(self) or force):
            self._current = -1
            return None
        self._current += 1
        if self._current >= super().__len__(): self._current = 0
        if self[self._current] or force:
            return self[self._current]
        else:
            return self.next()

    async def wait_next(self, *, force = False):
        # await asyncio.sleep()
        self.next(force=force)

    def prev(self, force = False):
        if not (any(self) or force):
            self._current = -1
            return None
        self._current -= 1
        if self._current < 0: self._current = super().__len__() - 1
        if self[self._current] or force:
            return self[self._current]
        else:
            return self.prev()
        
    @property
    def current(self):
        if 0 <= self._current < super().__len__():
            return self[self._current]
        
    @property
    def next_change_time(self):
        return self._next_change_time """



class _Config(BaseModel):
    assets:      list[Asset]                = Field([], alias='urls')
    def_duration:int                       = Field(_def_dur, alias='default_duration', ge=0)
    users:       dict[str, dict[str, str]] = {
        'admin': { #default user: name=admin; password=admin (pre-hashed)
            'pass': 'pbkdf2:sha256:260000$Q9SjfHgne5TOB3rb$f2c264b00585135a0c19930ea60e35d45ed862e8c6245d513c45f3f42df51d4c'
        }
    }
    # _current:str = ''
    filename:    Union[str, Path]          = Field('/etc/unitotem/unitotem.conf', exclude=True)
    first_boot:  bool                      = Field(True, exclude=True)
    
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

    def remove_asset(self, asset:Asset|str, missing_ok:bool = True):
        try:
            if isinstance(asset, str):
                asset = self.get_asset(asset)[1]
            self.assets.remove(asset)
        except ValueError:
            # element to delete is not in schedule
            if not missing_ok:
                raise

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






class FileInfo(BaseModel):
    filename: str = Field(allow_mutation=False)
    duration: Optional[str] = Field(allow_mutation=False)
    duration_s: int = Field(Config.def_duration, allow_mutation=False)
    size: str = Field(allow_mutation=False)

    class Config:
        validate_assignment = True





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
        return {k:v.dict() for k,v in self._files_info.items()}

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
                if isinstance(infile.read(), Coroutine):
                    while buf := await infile.read(_buf_size): 
                        await out.write(buf)
                else:
                    while buf := infile.read(_buf_size):
                        await out.write(buf)
        except FileNotFoundError:
            self.mkdirs() #create directory if not exists
            return await self.save(infile, out_filename)
        
        return out_filename

    def mkdirs(self):
        self._folder.mkdir(parents=True, exist_ok=True)

    def exists(self, file):
        return self._folder.joinpath(file).exists()

    def remove(self, file):
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
        for track in cast(list[Track], MediaInfo.parse(f).tracks): # type: ignore
            track_data = track.to_data()
            if 'duration' in track_data:
                dur = track_data.get('other_duration', [None])[0]
                dur_s = round(int(track_data['duration'])/1000)
                break
    return FileInfo(filename=basename(f), duration=dur, duration_s=dur_s,
        size=human_readable_size(getsize(f)))


def get_dominant_color(pil_img, palette_size=16): # https://stackoverflow.com/a/61730849/9655651
    # Resize image to speed up processing
    img = pil_img.copy()
    img.thumbnail((100, 100))
    # Reduce colors (uses k-means internally)
    paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)
    # Find the color that occurs most often
    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index*3:palette_index*3+3]
    return hex((dominant_color[0]<<16) + (dominant_color[1]<<8) + dominant_color[2])
