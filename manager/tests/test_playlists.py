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


# ── multi-page PDFs: `duration` is the time of each page ────────────────────

def test_a_multi_page_asset_advances_page_by_page_then_moves_on(manager):
    a = manager.default
    a.append({'url': 'file:slides.pdf', 'duration': 5, 'enabled': True, 'pages': 3})
    a.append({'url': 'https://example.com/next', 'duration': 5, 'enabled': True})
    a.next_a()
    assert a.current.url == 'file:slides.pdf' and a.current_page == 0
    a._timer_fired()
    assert a.current.url == 'file:slides.pdf' and a.current_page == 1
    a._timer_fired()
    assert a.current_page == 2
    a._timer_fired()
    assert a.current.url == 'https://example.com/next' and a.current_page == 0


def test_skipping_forward_resets_the_page(manager):
    a = manager.default
    a.append({'url': 'file:slides.pdf', 'duration': 5, 'enabled': True, 'pages': 3})
    a.append({'url': 'https://example.com/next', 'duration': 5, 'enabled': True})
    a.next_a()
    a._timer_fired()
    assert a.current_page == 1
    a.next_a()
    a.prev_a()
    assert a.current.url == 'file:slides.pdf' and a.current_page == 0


def test_show_payload_for_a_pdf_page_points_at_the_pdf_view_page(manager):
    from webview.controller import show_payload
    a = manager.default
    a.append({'url': 'file:my slides.pdf', 'duration': 5, 'enabled': True, 'pages': 2, 'fit': 1})
    payload = show_payload(a.assets[0], 0, 1)
    assert payload['container'] == 1   # the browser container
    assert payload['src'].startswith('https://localhost/pdf-view?')
    assert 'file=my+slides.pdf' in payload['src'] and 'page=2' in payload['src'] and 'fit=cover' in payload['src']


def test_none_duration_means_the_playlist_default(manager):
    a = manager.default
    a.default_duration = 12
    a.append({'url': 'file:picture.png', 'duration': None})
    assert a.assets[0].duration == 12


# ── the date parsing and tree helpers that replaced python-benedict ─────────

def test_parse_datetime_accepts_what_the_ui_and_saved_files_hold():
    from datetime import datetime
    from utils.objs import parse_datetime
    assert parse_datetime(None) is None and parse_datetime('') is None
    assert parse_datetime('2026-10-12T21:30:00') == datetime(2026, 10, 12, 21, 30)
    assert parse_datetime('2026-10-12T21:30:00Z').year == 2026
    assert parse_datetime(datetime(2026, 1, 1)) == datetime(2026, 1, 1)
    assert parse_datetime(0) == datetime.fromtimestamp(0)
    assert parse_datetime('12 Oct 2026 21:30') == datetime(2026, 10, 12, 21, 30)


def test_unflatten_builds_the_api_tree():
    from utils.objs import unflatten
    assert unflatten({'Settings/Audio/devices': 'a', 'Settings/Audio/set_mute': 'b', 'Power/reboot': 'c'}) == {
        'Settings': {'Audio': {'devices': 'a', 'set_mute': 'b'}}, 'Power': {'reboot': 'c'}}
