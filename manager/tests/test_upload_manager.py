"""Saving an upload: a missing folder is created before the stream is read, and a failure part-way leaves no partial file."""
import asyncio
import errno
import json
import zipfile
from io import BytesIO
from pathlib import Path
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


# ── backup restore: every archived file under its own name ───────────────────

def _seed(folder: Path, files: dict):
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def _listing(folder: Path) -> dict:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def _zip(members: dict):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return zipfile.ZipFile(BytesIO(buffer.getvalue()))


class ChunkedStream:
    """A file-like object that fails after its data is read."""
    def __init__(self, data: bytes, fail: bool = False):
        self._data, self._fail = data, fail

    def read(self, size):
        if self._data:
            chunk, self._data = self._data[:size], self._data[size:]
            return chunk
        if self._fail:
            raise FileNotFoundError('source vanished')
        return b''


@pytest.fixture
def backup_module(monkeypatch):
    import utils.backup as backup
    monkeypatch.setattr(uploadmanager, 'get_file_info', lambda path: FileInfo(filename=path.name, size='1 B', mime='text/plain'))
    return backup


@pytest.fixture
def restore_site(tmp_path, monkeypatch, backup_module):
    """A real upload manager and a real playlists manager (files in tmp), bound to the backup module."""
    from utils.models.playlists import PlaylistsManager
    asyncio.set_event_loop(asyncio.new_event_loop())      # asyncio.run of the previous test closed the loop
    root = tmp_path / 'site'
    root.mkdir()
    um = uploadmanager.UploadManager(root / 'uploaded')
    pm = PlaylistsManager()
    pm._index_path = lambda: root / 'playlists.json'
    pm._playlist_path = lambda pid: root / f'playlist_{pid}.json'
    default = pm.create('Main')
    pm.set_default(default.playlist_id)
    monkeypatch.setattr(backup_module, 'upload_manager', um)
    monkeypatch.setattr(backup_module, 'playlists_manager', pm)
    return SimpleNamespace(manager=um, playlists=pm, folder=um.folder, root=root)


def _playlist_archive(playlists) -> dict:
    """The playlists part of an archive: the default playlist with one 'file:x.txt' asset."""
    am = playlists.default
    am.append({'name': 'x.txt', 'url': 'file:x.txt', 'duration': 10, 'enabled': True, 'media_type': 'text/plain',
               'pages': 1})
    return {'playlists': {am.playlist_id: json.loads(am.model_dump_json())}, 'default': am.playlist_id}


def _playlist_state(playlists) -> list:
    return [(a.name, a.url, a.duration, a.enabled) for a in playlists.default.assets]


def test_a_restored_file_replaces_the_file_of_the_same_name(restore_site, backup_module, playlist):
    asyncio.run(_test_a_restored_file_replaces_the_file_of_the_same_name(restore_site, backup_module, playlist))


async def _test_a_restored_file_replaces_the_file_of_the_same_name(site, backup_module, playlist):
    _seed(site.folder, {'x.txt': b'old'})
    await backup_module._restore_files(_zip({'uploaded/x.txt': b'new'}))
    assert _listing(site.folder) == {'x.txt': b'new'}
    assert playlist.appended == [] and playlist.saved == []       # no asset created


def test_restoring_twice_gives_the_same_files_and_playlist(restore_site, backup_module):
    asyncio.run(_test_restoring_twice_gives_the_same_files_and_playlist(restore_site, backup_module))


async def _test_restoring_twice_gives_the_same_files_and_playlist(site, backup_module):
    archive = {'uploaded/x.txt': b'new', 'uploaded/y.txt': b'yy'}
    playlists_data = _playlist_archive(site.playlists)
    for _ in range(2):
        await backup_module._restore_playlists(playlists_data, '3.0.0')
        await backup_module._restore_files(_zip(archive))
    assert _listing(site.folder) == {'x.txt': b'new', 'y.txt': b'yy'}
    assert _playlist_state(site.playlists) == [('x.txt', 'file:x.txt', 10, True)]


@pytest.mark.parametrize('order', ['playlists-first', 'uploads-first', 'uploads-only'])
def test_playlists_and_uploads_give_the_same_result_in_either_order(tmp_path, monkeypatch, backup_module, order):
    asyncio.run(_test_playlists_and_uploads_give_the_same_result_in_either_order(tmp_path, monkeypatch, backup_module,
                                                                                order))


async def _test_playlists_and_uploads_give_the_same_result_in_either_order(tmp_path, monkeypatch, backup_module, order):
    from utils.models.playlists import PlaylistsManager
    monkeypatch.setattr(uploadmanager, 'get_file_info', lambda path: FileInfo(filename=path.name, size='1 B', mime='text/plain'))
    source_root = tmp_path / 'source'
    source_root.mkdir()
    source = PlaylistsManager()
    source._index_path = lambda: source_root / 'playlists.json'
    source._playlist_path = lambda pid: source_root / f'playlist_{pid}.json'
    source.set_default(source.create('Main').playlist_id)
    playlists_data = _playlist_archive(source)
    files = _zip({'uploaded/x.txt': b'new'})

    states = {}
    for name in ('a', 'b'):
        site_root = tmp_path / name
        site_root.mkdir()
        _seed(site_root / 'uploaded', {'x.txt': b'old'})
        um = uploadmanager.UploadManager(site_root / 'uploaded')
        pm = PlaylistsManager()
        pm._index_path = lambda root=site_root: root / 'playlists.json'
        pm._playlist_path = lambda pid, root=site_root: root / f'playlist_{pid}.json'
        pm.set_default(pm.create('Main').playlist_id)
        monkeypatch.setattr(backup_module, 'upload_manager', um)
        monkeypatch.setattr(backup_module, 'playlists_manager', pm)
        if order == 'uploads-only':
            await backup_module._restore_files(files)
        elif (order == 'playlists-first') == (name == 'a'):
            await backup_module._restore_playlists(playlists_data, '3.0.0')
            await backup_module._restore_files(_zip({'uploaded/x.txt': b'new'}))
        else:
            await backup_module._restore_files(_zip({'uploaded/x.txt': b'new'}))
            await backup_module._restore_playlists(playlists_data, '3.0.0')
        states[name] = (_listing(um.folder), _playlist_state(pm))
    assert states['a'] == states['b']
    if order == 'uploads-only':
        assert states['a'][0] == {'x.txt': b'new'}
    else:
        assert states['a'] == ({'x.txt': b'new'}, [('x.txt', 'file:x.txt', 10, True)])


@pytest.mark.parametrize('name', [
    'uploaded/../evil.txt', 'uploaded/a/b.txt', 'uploaded/..\\x.txt', 'uploaded/a\x01b.txt', 'uploaded/' + 'a' * 300,
    'uploaded//etc/x', 'uploaded/', 'uploaded//',
])
def test_an_unsafe_archive_name_is_skipped_and_the_safe_sibling_restored(restore_site, backup_module, name):
    asyncio.run(_test_an_unsafe_archive_name_is_skipped_and_the_safe_sibling_restored(restore_site, backup_module, name))


async def _test_an_unsafe_archive_name_is_skipped_and_the_safe_sibling_restored(site, backup_module, name):
    await backup_module._restore_files(_zip({name: b'bad', 'uploaded/safe-name.txt': b'safe'}))
    assert _listing(site.folder) == {'safe-name.txt': b'safe'}
    written = {p.relative_to(site.root).as_posix() for p in site.root.rglob('*')}
    assert written == {'uploaded', 'uploaded/safe-name.txt', 'playlist_' + site.playlists.default.playlist_id + '.json',
                       'playlists.json'}


@pytest.mark.parametrize('name', ['uploaded/', '', 'uploaded/a/b', 'uploaded/..', 'uploaded/.', 'uploaded/\x7f',
                                  'uploaded/' + 'b' * 256])
def test_a_plain_filename_refuses_every_unsafe_name(name):
    with pytest.raises(ValueError):
        uploadmanager.plain_filename(name)


def test_the_prefix_is_optional_and_a_safe_name_is_kept():
    assert uploadmanager.plain_filename('uploaded/clip.mp4') == 'clip.mp4'
    assert uploadmanager.plain_filename('clip.mp4') == 'clip.mp4'


def test_only_the_first_ten_thousand_archived_files_are_considered(backup_module, monkeypatch, tmp_path):
    asyncio.run(_test_only_the_first_ten_thousand_archived_files_are_considered(backup_module, monkeypatch, tmp_path))


async def _test_only_the_first_ten_thousand_archived_files_are_considered(backup_module, monkeypatch, tmp_path):
    names = []

    async def restore(infile, name, max_bytes=None, size=None):
        names.append(name)

    stub = SimpleNamespace(folder=tmp_path, disk_total=0, restore=restore)
    monkeypatch.setattr(backup_module, 'upload_manager', stub)
    members = {f'uploaded/f{i}.txt': b'' for i in range(10001)}
    await backup_module._restore_files(_zip(members))
    assert len(names) == backup_module._MAX_RESTORED_FILES == 10000
    assert names[-1] == 'uploaded/f9999.txt'


def test_a_total_above_the_free_space_minus_the_reserve_is_refused_and_nothing_is_written(restore_site, backup_module,
                                                                                         monkeypatch):
    asyncio.run(_test_a_total_above_the_free_space_minus_the_reserve_is_refused(restore_site, backup_module, monkeypatch))


async def _test_a_total_above_the_free_space_minus_the_reserve_is_refused(site, backup_module, monkeypatch):
    site.manager._disk_total = 10_000                     # reserve: 5% = 500 bytes
    monkeypatch.setattr(backup_module, 'disk_usage', lambda path: SimpleNamespace(free=1000))
    _seed(site.folder, {'x.txt': b'old'})
    with pytest.raises(ValueError, match='Not enough disk space'):
        await backup_module._restore_files(_zip({'uploaded/x.txt': b'n' * 300, 'uploaded/y.txt': b'n' * 301}))
    assert _listing(site.folder) == {'x.txt': b'old'}


def test_restore_with_a_size_above_the_free_space_is_enospc_before_writing(restore_site, monkeypatch):
    asyncio.run(_test_restore_with_a_size_above_the_free_space_is_enospc_before_writing(restore_site, monkeypatch))


async def _test_restore_with_a_size_above_the_free_space_is_enospc_before_writing(site, monkeypatch):
    monkeypatch.setattr(uploadmanager, 'disk_usage', lambda path: SimpleNamespace(free=10))
    with pytest.raises(OSError) as raised:
        await site.manager.restore(BytesIO(b'x' * 100), 'big.bin', size=100)
    assert raised.value.errno == errno.ENOSPC
    assert _listing(site.folder) == {}


def test_streaming_over_the_free_space_is_enospc_and_leaves_no_temp_file(restore_site, monkeypatch):
    asyncio.run(_test_streaming_over_the_free_space_is_enospc_and_leaves_no_temp_file(restore_site, monkeypatch))


async def _test_streaming_over_the_free_space_is_enospc_and_leaves_no_temp_file(site, monkeypatch):
    monkeypatch.setattr(uploadmanager, 'disk_usage', lambda path: SimpleNamespace(free=10))
    with pytest.raises(OSError) as raised:
        await site.manager.restore(BytesIO(b'x' * 100), 'big.bin')
    assert raised.value.errno == errno.ENOSPC
    assert _listing(site.folder) == {}


def test_more_data_than_declared_is_refused_and_the_old_file_stays(restore_site):
    asyncio.run(_test_more_data_than_declared_is_refused_and_the_old_file_stays(restore_site))


async def _test_more_data_than_declared_is_refused_and_the_old_file_stays(site):
    _seed(site.folder, {'x.txt': b'old'})
    with pytest.raises(ValueError):
        await site.manager.restore(BytesIO(b'0123456789'), 'x.txt', max_bytes=5)
    assert _listing(site.folder) == {'x.txt': b'old'}


def test_a_read_error_mid_restore_keeps_the_old_files_and_leaves_no_temp(restore_site):
    asyncio.run(_test_a_read_error_mid_restore_keeps_the_old_files_and_leaves_no_temp(restore_site))


async def _test_a_read_error_mid_restore_keeps_the_old_files_and_leaves_no_temp(site):
    _seed(site.folder, {'x.txt': b'old', 'y.txt': b'keep'})
    with pytest.raises(FileNotFoundError):
        await site.manager.restore(ChunkedStream(b'0123456789', fail=True), 'x.txt')
    assert _listing(site.folder) == {'x.txt': b'old', 'y.txt': b'keep'}


def test_the_scan_callback_gets_the_restored_file_and_the_asset_is_satisfied(restore_site, backup_module):
    asyncio.run(_test_the_scan_callback_gets_the_restored_file_and_the_asset_is_satisfied(restore_site, backup_module))


async def _test_the_scan_callback_gets_the_restored_file_and_the_asset_is_satisfied(site, backup_module):
    calls = []

    async def scanned(data):
        calls.append(data)

    site.manager._callback = scanned                    # built inside the running loop, as the server does
    site.manager._evloop = asyncio.get_running_loop()
    playlists_data = _playlist_archive(site.playlists)
    await backup_module._restore_playlists(playlists_data, '3.0.0')
    await backup_module._restore_files(_zip({'uploaded/x.txt': b'new'}))
    await asyncio.sleep(0)
    assert any('x.txt' in data for data in calls)
    assert site.manager.filenames == ['x.txt']
    assert 'x.txt' in site.manager.files_info
    assert 'file:x.txt' in [a.url for a in site.playlists.default.assets]
    assert 'x.txt' in site.manager.filenames            # the asset's file is present
