"""Saving an upload: a missing folder is created before the stream is read, and a failure part-way leaves no partial file."""
import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest

try:
    from utils.storage import uploadmanager
except OSError:
    pytest.skip('/var/lib/unitotem is not writable here (runs in autopkgtest, as root)', allow_module_level=True)

from utils.storage.file_info import FileInfo


@pytest.fixture
def playlist(monkeypatch):
    """The default playlist that receives an upload: recorded, never saved to disk."""
    appended, saved = [], []
    default = SimpleNamespace(append=appended.append, save=lambda: saved.append(True))
    import utils.models.playlists as playlists
    monkeypatch.setattr(playlists, 'playlists_manager', SimpleNamespace(default=default))
    monkeypatch.setattr(uploadmanager, 'get_file_info', lambda path: FileInfo(filename=path.name, size='1 B', mime='text/plain'))
    return SimpleNamespace(appended=appended, saved=saved)


@pytest.fixture
def manager(tmp_path):
    asyncio.set_event_loop(asyncio.new_event_loop())      # asyncio.run of the previous test closed the loop
    return uploadmanager.UploadManager(tmp_path / 'uploaded')


class BrokenStream:
    """Gives one chunk, then the file it reads from is gone."""
    def __init__(self):
        self.calls = 0

    def read(self, size):
        self.calls += 1
        if self.calls == 1:
            return b'0123456789'
        raise FileNotFoundError('source vanished')


def test_a_missing_folder_is_created_and_the_file_saved(manager, playlist):
    asyncio.run(_test_a_missing_folder_is_created_and_the_file_saved(manager, playlist))


async def _test_a_missing_folder_is_created_and_the_file_saved(manager, playlist):
    out = await manager.save(BytesIO(b'hello'), 'clip.txt')
    assert out.read_bytes() == b'hello'
    assert out.parent == manager.folder
    assert [a['name'] for a in playlist.appended] == ['clip.txt']


def test_a_failure_mid_stream_leaves_no_partial_file(manager, playlist, monkeypatch):
    asyncio.run(_test_a_failure_mid_stream_leaves_no_partial_file(manager, playlist, monkeypatch))


async def _test_a_failure_mid_stream_leaves_no_partial_file(manager, playlist, monkeypatch):
    monkeypatch.setattr(uploadmanager, '_buf_size', 4)
    manager.mkdirs()
    with pytest.raises(FileNotFoundError):
        await manager.save(BrokenStream(), 'clip.txt')
    assert list(manager.folder.iterdir()) == []
    assert playlist.appended == [] and playlist.saved == []


def test_a_failed_save_does_not_make_a_renamed_copy_on_retry(manager, playlist, monkeypatch):
    asyncio.run(_test_a_failed_save_does_not_make_a_renamed_copy_on_retry(manager, playlist, monkeypatch))


async def _test_a_failed_save_does_not_make_a_renamed_copy_on_retry(manager, playlist, monkeypatch):
    monkeypatch.setattr(uploadmanager, '_buf_size', 4)
    manager.mkdirs()
    with pytest.raises(FileNotFoundError):
        await manager.save(BrokenStream(), 'clip.txt')
    out = await manager.save(BytesIO(b'whole'), 'clip.txt')
    assert out.name == 'clip.txt' and out.read_bytes() == b'whole'
    assert [p.name for p in manager.folder.iterdir()] == ['clip.txt']


def test_a_duplicate_name_gets_a_number(manager, playlist):
    asyncio.run(_test_a_duplicate_name_gets_a_number(manager, playlist))


async def _test_a_duplicate_name_gets_a_number(manager, playlist):
    manager.mkdirs()
    first = await manager.save(BytesIO(b'a'), 'clip.txt')
    second = await manager.save(BytesIO(b'b'), 'clip.txt')
    assert first.name == 'clip.txt' and second.name == 'clip_2.txt'
    assert second.read_bytes() == b'b'
