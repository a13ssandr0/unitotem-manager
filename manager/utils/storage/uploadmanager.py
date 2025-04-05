import asyncio
import os
from asyncio import iscoroutinefunction
from pathlib import Path
from shutil import disk_usage
from typing import Callable, Coroutine, Union

from aiofiles import open as aopen
from watchdog.events import FileSystemEventHandler
from werkzeug.utils import secure_filename

from api.models import Config
from utils.storage.file_info import FileInfo, get_file_info
from utils.units import human_readable_size

_buf_size = 64 * 1024 * 1024  # 64MB buffer


class UploadManager(FileSystemEventHandler):

    def __init__(self, folder: Path, scan_callback: Callable[[dict], Coroutine] | None = None):
        self._folder = folder
        self._folder.mkdir(exist_ok=True)

        self._files: list[Path] = []
        self._files_info: dict[str, FileInfo] = {}
        self._disk_used = 0
        self._disk_total = disk_usage(folder).total
        self._disk_totalh = human_readable_size(self._disk_total)
        self._callback = scan_callback
        self._evloop = asyncio.get_event_loop()

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
        return {k: v.model_dump() for k, v in self._files_info.items()}

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
                try:
                    f_info = get_file_info(file)
                    self._files.append(file)
                    self._files_info[file.name] = f_info
                except FileNotFoundError:
                    # when deleting multiple files while a scan is running, a race condition might occur so that file
                    # is present on disk both when iterating the folder and checking if file still exixst, but it might
                    # be deleted for when `get_file_info` is starting to process the file
                    pass
        self._disk_used = disk_usage(self._folder).used
        if self._callback is not None and self._evloop is not None:
            self._evloop.create_task(self._callback(self.serialize()))

    def create_filename(self, filename: Union[str, Path, None]):
        if filename is None:
            filename = ''

        if isinstance(filename, str):
            filename = Path(filename)

        filename = self._folder.joinpath(
            secure_filename(filename.name) or os.urandom(4).hex()
        )
        # allow files with duplicate filenames, simply add a number at the end
        if filename.exists():
            stem = filename.stem + '_{}'
            i = 1
            while filename.exists():
                i += 1
                filename = filename.with_stem(stem.format(i))
        return filename

    async def save(self, infile, out_filename=None) -> Path:
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
            self.mkdirs()  # create directory if not exists
            return await self.save(infile, out_filename)

        file_data = get_file_info(out_filename)
        Config.assets.append({
            'name': file_data.filename,
            'url': 'file:' + file_data.filename,
            'duration': file_data.duration_s,
            'enabled': False,
            'media_type': file_data.mime
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
        Config.save()
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
