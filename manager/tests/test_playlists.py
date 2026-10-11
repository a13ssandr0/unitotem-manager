"""Playlist manager: renaming, default selection, copying/moving assets, per-playlist default duration."""
import asyncio

import pytest

from utils.models import playlists as playlists_module
from utils.models.playlists import PlaylistsManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setattr(PlaylistsManager, '_playlist_path', lambda self, pid: tmp_path / f'playlist_{pid}.json')
    monkeypatch.setattr(PlaylistsManager, '_index_path', lambda self: tmp_path / 'playlists.json')
    m = PlaylistsManager()
    first = m.create('First')
    m._default_id = first.playlist_id
    m._save_index()
    return m


def test_rename_and_refuse_empty_name(manager):
    pid = manager.default.playlist_id
    manager.rename(pid, '  Lobby ')
    assert manager.get(pid).name == 'Lobby'
    with pytest.raises(ValueError):
        manager.rename(pid, '   ')
    with pytest.raises(KeyError):
        manager.rename('nope', 'x')


def test_default_cannot_be_deleted_until_another_is_chosen(manager):
    other = manager.create('Other')
    first = manager.default.playlist_id
    with pytest.raises(ValueError):
        manager.delete(first)
    manager.set_default(other.playlist_id)
    manager.delete(first)
    assert list(manager.playlists) == [other.playlist_id]


def test_default_duration_is_per_playlist(manager):
    a, b = manager.default, manager.create('B')
    a.default_duration = 10
    b.default_duration = 45
    a.append({'url': 'https://example.com/a'})
    b.append({'url': 'https://example.com/b'})
    assert a.assets[-1].duration == 10
    assert b.assets[-1].duration == 45


def test_copy_gives_new_uuid_and_move_removes_from_source(manager):
    a, b = manager.default, manager.create('B')
    a.append({'url': 'https://example.com/x', 'name': 'x', 'duration': 5})
    uuid = a.assets[0].uuid
    created = manager.transfer_assets([uuid], a.playlist_id, b.playlist_id)
    assert len(a.assets) == 1 and len(b.assets) == 1
    assert created == [b.assets[0].uuid] and created[0] != uuid
    assert b.assets[0].name == 'x' and b.assets[0].duration == 5
    manager.transfer_assets([uuid], a.playlist_id, b.playlist_id, move=True)
    assert len(a.assets) == 0 and len(b.assets) == 2


def test_transfer_to_itself_is_refused(manager):
    with pytest.raises(ValueError):
        manager.transfer_assets([], manager.default.playlist_id, manager.default.playlist_id)


def test_referenced_files_lists_every_playlist_using_a_file(manager):
    a, b = manager.default, manager.create('B')
    a.append({'url': 'file:clip.mp4'})
    b.append({'url': 'file:clip.mp4'})
    b.append({'url': 'https://example.com'})
    assert sorted(manager.referenced_files()['clip.mp4']) == sorted([a.playlist_id, b.playlist_id])
    assert 'https://example.com' not in manager.referenced_files()


def test_volume_and_mute_default_and_validation(manager):
    a = manager.default
    a.append({'url': 'file:clip.mp4'})
    assert a.assets[0].volume == 1.0 and a.assets[0].muted is False
    with pytest.raises(Exception):
        a.append({'url': 'file:loud.mp4', 'volume': 2})
