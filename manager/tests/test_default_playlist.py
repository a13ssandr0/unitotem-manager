"""The default playlist as the content of every pane that has no playlist of its own: what an unassigned pane shows, what
changes when the default changes, when a playlist is deleted, when playlists are replaced by a backup restore, and the
screen assignments kept in a backup (export, restore, and hostile archives)."""
import asyncio
import json

import pytest
from loguru import logger

import utils.backup as backup
from api.ws.endpoints import WS
from utils.models.assets import AssetsManager, no_assets
from utils.models.command_line import cmdargs
from utils.models.playlists import playlists_manager
from utils.remote import viewers
from webview import controller
from webview.controller import PlaylistLoop, WebviewManager
from test_layout_controller import VIEWER, FakePlaylist, make_manager, run, settle, src, window

@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Nothing is started, nothing is written outside tmp, and the idle page is a known asset."""
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setitem(cmdargs.__dict__, 'assets_file', tmp_path / 'assets.json')
    monkeypatch.setattr(PlaylistLoop, 'start', lambda self: None)       # no playback loop runs
    monkeypatch.setattr(playlists_manager, '_playlists', {})
    monkeypatch.setattr(playlists_manager, '_default_id', None)
    monkeypatch.setattr(WebviewManager, '_instance', None)
    monkeypatch.setattr(playlists_manager, '_binder', None)       # no client push in these tests
    monkeypatch.setattr(controller, 'idle_asset', lambda: no_assets)
    yield


def setup(tmp_path, monkeypatch, ids=('A', 'B', 'C'), default='A', windows=(0,)):
    """A manager with loops A, B, C and the given playlists (fake ones) in the global playlists manager."""
    for pid in ids:
        playlists_manager._playlists[pid] = FakePlaylist(pid)
    playlists_manager._default_id = default
    return make_manager(tmp_path, monkeypatch, windows=windows)


def real_playlist(pid, name, url=None):
    am = AssetsManager(playlist_id=pid, name=name)
    if url:
        am.append({'url': url})
    return am


def dumped(am):
    return json.loads(am.model_dump_json())


# ── what an unassigned pane shows ────────────────────────────────────────

def test_an_unassigned_pane_plays_the_default_playlist(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A')
        await settle()
        shows = ws.shows(0, 1)
        assert [src(s) for s in shows] == ['https://A.test/'] and shows[0]['idle'] is False
        assert (VIEWER, 0, 1) in mgr._loops['A'].assigned_webviews
        assert mgr.get_assignment(VIEWER, 0) is None                 # the default is not an assignment
    run(go())


def test_a_default_change_moves_the_unassigned_pane_to_the_new_default(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A')
        await settle()
        ws.sent.clear()
        playlists_manager._default_id = 'B'
        mgr.default_changed()
        await settle()
        assert [src(s) for s in ws.shows(0, 1)] == ['https://B.test/']
        assert (VIEWER, 0, 1) not in mgr._loops['A'].assigned_webviews
        assert (VIEWER, 0, 1) in mgr._loops['B'].assigned_webviews
    run(go())


def test_an_explicitly_assigned_pane_ignores_a_default_change(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A')
        mgr.assign(VIEWER, 0, 'C')
        await settle()
        ws.sent.clear()
        playlists_manager._default_id = 'B'
        mgr.default_changed()
        await settle()
        assert ws.shows(0, 1) == []                                  # nothing is sent to it
        assert (VIEWER, 0, 1) in mgr._loops['C'].assigned_webviews
        assert (VIEWER, 0, 1) not in mgr._loops['B'].assigned_webviews
    run(go())


def test_a_deleted_playlist_falls_back_to_the_default(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A')
        mgr.assign(VIEWER, 0, 'C')
        await settle()
        ws.sent.clear()
        playlists_manager._playlists.pop('C')
        mgr.remove_playlist_loop('C')
        await settle()
        assert [src(s) for s in ws.shows(0, 1)] == ['https://A.test/']
        assert mgr.get_assignment(VIEWER, 0) is None
        assert (VIEWER, 0, 1) in mgr._loops['A'].assigned_webviews
    run(go())


@pytest.mark.parametrize('ids, default', [((), None), (('N',), 'N')], ids=['no default', 'default without a loop'])
def test_without_a_default_playlist_the_pane_shows_the_idle_page(tmp_path, monkeypatch, ids, default):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, ids=ids, default=default)
        assert mgr.default_playlist_id() is None
        await settle()
        assert [(src(s), s['idle'], s['pane']) for s in ws.shows(0, 1)] == [(no_assets.url, True, 1)]
        assert not any((VIEWER, 0, 1) in loop.assigned_webviews for loop in mgr._loops.values())
    run(go())


def test_unassigning_the_pane_makes_it_follow_the_default_again(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A')
        mgr.assign(VIEWER, 0, 'C')
        await settle()
        ws.sent.clear()
        mgr.unassign(VIEWER, 0)
        await settle()
        assert [src(s) for s in ws.shows(0, 1)] == ['https://A.test/']
        assert (VIEWER, 0, 1) in mgr._loops['A'].assigned_webviews
        assert not mgr._loops['C'].assigned_webviews
    run(go())


# ── playlists replaced as a whole (backup restore) ───────────────────────

def test_replacing_the_playlists_rebuilds_the_loops_and_drops_the_gone_assignments(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A', windows=(0,))
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0), window(1, 'DP-1')], 'layout': True})
        mgr.assign(VIEWER, 0, 'C')                                   # C goes away
        mgr.assign(VIEWER, 1, 'B')                                   # B stays
        await settle()
        old_b = mgr._loops['B']

        playlists_manager._playlists.clear()
        for pid in ('B', 'N'):
            playlists_manager._playlists[pid] = FakePlaylist(pid)
        playlists_manager._default_id = 'N'
        ws.sent.clear()
        mgr.playlists_replaced()
        await settle()

        assert sorted(mgr._loops) == ['B', 'N']
        assert mgr._loops['B'] is not old_b and mgr._loops['B'].playlist is playlists_manager._playlists['B']
        assert mgr.get_assignment(VIEWER, 0) is None                 # the assignment to C is dropped
        assert mgr.get_assignment(VIEWER, 1) == 'B'                  # the one to B is kept
        assert [src(s) for s in ws.shows(0, 1)] == ['https://N.test/']
        assert (VIEWER, 1, 1) in mgr._loops['B'].assigned_webviews
        saved = json.loads((tmp_path / 'viewer_assignments.json').read_text())
        assert saved == [{'webview_id': VIEWER, 'screen_name': 'DP-1', 'playlist_id': 'B'}]
    run(go())


# ── backup: export of the screen assignments ─────────────────────────────

def test_the_export_carries_the_screen_assignments_beside_the_playlists(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, ids=(), default='A')
        playlists_manager._playlists.update({'A': real_playlist('A', 'Alpha'), 'B': real_playlist('B', 'Bravo')})
        mgr.assign(VIEWER, 0, 'B')
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        exported = backup._export_playlists()
        assert exported['assignments'] == {'version': 1, 'screens': {'HDMI-A-1': 'B'}}
        assert set(exported['playlists']) == {'A', 'B'} and exported['default'] == 'A'
        assert exported['playlists']['B'] == dumped(playlists_manager._playlists['B'])
    run(go())


def test_the_export_leaves_out_assignments_to_playlists_that_are_not_in_the_archive(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, ids=(), default='A', windows=(0, 1))
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0), window(1, 'DP-1')], 'layout': True})
        playlists_manager._playlists.update({'A': real_playlist('A', 'Alpha'), 'B': real_playlist('B', 'Bravo')})
        mgr.assign(VIEWER, 0, 'B')
        mgr.assign(VIEWER, 1, 'C')                                   # a loop, but no playlist of the node any more
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        assert backup._export_playlists()['assignments'] == {'version': 1, 'screens': {'HDMI-A-1': 'B'}}
    run(go())


# ── backup: restore of the screen assignments ────────────────────────────

class Recorder:
    def __init__(self):
        self.broadcasts = []
        self.publishes = []

    async def broadcast(self, target, /, nocache=False, **kwargs):
        self.broadcasts.append((target, kwargs))

    async def publish(self, vm):
        self.publishes.append(vm)


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(WS, 'broadcast', rec.broadcast)            # no network
    monkeypatch.setattr(viewers, 'publish', rec.publish)
    return rec


def archive(playlists, default, assignments=None):
    data = {'playlists': {am.playlist_id: dumped(am) for am in playlists}, 'default': default}
    if assignments is not None:
        data['assignments'] = assignments
    return data


def test_restore_applies_the_screen_assignments_live_and_keeps_the_existing_ones(tmp_path, monkeypatch, recorder):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A', windows=(0,))
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0), window(1, 'DP-1')], 'layout': True})
        mgr.assign(VIEWER, 0, 'C')                                   # an existing assignment: it stays
        await settle()
        ws.sent.clear()
        data = archive([real_playlist('B', 'Bravo', 'https://bravo.test/'), real_playlist('C', 'Charlie')], 'B',
                       {'version': 1, 'screens': {'HDMI-A-1': 'B', 'DP-1': 'B'}})
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        await backup._restore_playlists(data, '0')
        await settle()

        assert mgr.get_assignment(VIEWER, 0) == 'C'
        assert mgr.get_assignment(VIEWER, 1) == 'B'                  # applied by screen name, to the window now there
        assert (VIEWER, 1, 1) in mgr._loops['B'].assigned_webviews
        assert [src(s) for s in ws.shows(1, 1)] == [playlists_manager._playlists['B'].current.url]
        assert playlists_manager._default_id == 'B'
        assert any(t == 'Scheduler/playlists' for t, _ in recorder.broadcasts)
    run(go())


def test_restore_of_an_archive_without_assignments_restores_the_playlists_as_before(tmp_path, monkeypatch, recorder):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, default='A', windows=(0,))
        mgr.assign(VIEWER, 0, 'A')
        await settle()
        messages = []
        sink = logger.add(lambda m: messages.append(m), level='WARNING')
        try:
            data = archive([real_playlist('D', 'Delta', 'https://delta.test/')], 'D')     # no 'assignments' key
            monkeypatch.setattr(WebviewManager, '_instance', mgr)
            await backup._restore_playlists(data, '0')
        finally:
            logger.remove(sink)
        assert messages == []                                       # not a warning, not a failure
        assert set(playlists_manager.playlists) == {'D'} and playlists_manager._default_id == 'D'
        assert mgr.get_assignment(VIEWER, 0) is None                # 'A' is gone with its playlist
        assert (VIEWER, 0, 1) in mgr._loops['D'].assigned_webviews  # the unassigned pane follows the restored default
        assert any(t == 'Scheduler/playlists' for t, _ in recorder.broadcasts)
    run(go())


def test_a_legacy_config_keeps_its_playlists_part():
    legacy = {'CONFIG': {'playlists': [{'playlist_id': 'A'}], 'playlists_default': 'A', 'users': {}},
              'hostname': 'totem', 'def_audio_dev': 'hdmi'}
    out = backup.from_legacy(legacy)
    assert out['playlists'] == {'playlists': [{'playlist_id': 'A'}], 'default': 'A'}
    assert 'assignments' not in out['playlists']
    as_string = backup.from_legacy({'CONFIG': json.dumps(legacy['CONFIG'])})
    assert as_string['playlists'] == out['playlists']
    # a part that is already in the archive is not replaced by the legacy one
    assert backup.from_legacy({**legacy, 'parts': {'playlists': {'playlists': [], 'default': None}}})['playlists'] == {
        'playlists': [], 'default': None}


# ── hostile archives and input ───────────────────────────────────────────

def test_a_screen_map_that_is_not_a_dict_applies_nothing(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch)
        for screens in (['HDMI-A-1', 'B'], None, 'HDMI-A-1', 5, ('HDMI-A-1', 'B')):
            assert mgr.apply_screen_assignments(VIEWER, screens) == 0
        assert mgr.screen_assignments(VIEWER) == {}
    run(go())


def test_screen_names_that_are_not_strings_applies_nothing(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch)
        assert mgr.apply_screen_assignments(VIEWER, {1: 'B', None: 'B', ('HDMI-A-1',): 'B'}) == 0
        assert mgr.screen_assignments(VIEWER) == {}
    run(go())


def test_playlist_ids_that_are_unknown_or_not_strings_apply_nothing(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch)
        screens = {'HDMI-A-1': 123, 'DP-1': ['x'], 'HDMI-A-2': 'nope', 'HDMI-A-3': None, 'HDMI-A-4': {'id': 'B'}}
        assert mgr.apply_screen_assignments(VIEWER, screens) == 0
        assert mgr.screen_assignments(VIEWER) == {}
    run(go())


def test_control_characters_and_path_like_names_are_refused_and_valid_ones_still_apply(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch, windows=(0, 1))
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0), window(1, 'DP-1')], 'layout': True})
        hostile = {'HDMI\n1': 'B', '\x00': 'B', '../x': 'B', '': 'B', 'X' * 65: 'B', 'a b': 'B'}
        assert mgr.apply_screen_assignments(VIEWER, hostile) == 0
        assert mgr.screen_assignments(VIEWER) == {}
        assert mgr.apply_screen_assignments(VIEWER, {**hostile, 'DP-1': 'B'}) == 1
        assert mgr.screen_assignments(VIEWER) == {'DP-1': 'B'}
    run(go())


def test_a_huge_screen_map_is_capped_at_64_entries(tmp_path, monkeypatch):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch)
        huge = {f'S{i}': 'B' for i in range(10_000)}
        applied = mgr.apply_screen_assignments(VIEWER, huge)
        assert 0 < applied <= 64
        assert len(mgr.screen_assignments(VIEWER)) == applied
    run(go())


@pytest.mark.parametrize('assignments', [
    {'version': 2, 'screens': {'HDMI-A-1': 'B'}},
    'str',
    {'version': 1, 'screens': [1]},
    {'version': True},
    {'version': True, 'screens': {'HDMI-A-1': 'B'}},
    {'version': 1},
    [],
    5,
])
def test_malformed_assignments_of_an_archive_are_ignored_without_error(tmp_path, monkeypatch, assignments):
    async def go():
        mgr, ws = setup(tmp_path, monkeypatch)
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        backup._restore_assignments(assignments)                     # must not raise
        assert mgr.screen_assignments(VIEWER) == {}
    run(go())
