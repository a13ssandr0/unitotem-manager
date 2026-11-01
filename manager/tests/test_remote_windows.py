"""The windows of a node travel with its playlists when it is adopted or unpaired: their settings per screen and their layouts, with the panes' playlists
under the new names they get on arrival."""
import json

import pytest

from utils.models import layout as layout_module
from utils.models.layout import WindowLayout, WindowLayouts, split_leaf, with_pane_playlist
from utils.models.viewer import KeyboardSettings, ScreensaverSettings, viewer_settings
from utils.remote import bundle, transfer


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """A clean set of per-window settings and layouts, never the real files."""
    layouts = WindowLayouts(tmp_path / 'window_layouts.json')
    monkeypatch.setattr(layout_module, 'window_layouts', layouts)
    monkeypatch.setattr(type(viewer_settings), 'save', lambda self: None)
    for name in bundle.WINDOW_FIELDS:
        setattr(viewer_settings, name, {})
    yield layouts
    for name in bundle.WINDOW_FIELDS:
        setattr(viewer_settings, name, {})


def divided(playlist: str) -> WindowLayout:
    layout, pane = split_leaf(WindowLayout(), 1, 'h')
    return with_pane_playlist(layout, pane, playlist)


def set_up_window(viewer: str, screen: str, panes_playlist: str = 'pl-pane'):
    key = f'{viewer}|{screen}'
    viewer_settings.audio_outputs = {**viewer_settings.audio_outputs, key: 'HDMI Audio'}
    viewer_settings.keyboards = {**viewer_settings.keyboards, key: KeyboardSettings(enabled=False, layout='it')}
    viewer_settings.screensavers = {**viewer_settings.screensavers, key: ScreensaverSettings(enabled=True, home_url='https://example.com/', idle_timeout=90)}
    viewer_settings.orientations = {**viewer_settings.orientations, key: 3}
    viewer_settings.flips = {**viewer_settings.flips, key: 1}
    viewer_settings.languages = {**viewer_settings.languages, key: 'de'}
    layout_module.window_layouts.put(key, divided(panes_playlist))


def test_the_panes_playlists_of_a_leaving_node_go_with_it():
    files = {'main': {'a.jpg'}, 'pane': {'b.jpg'}, 'other': {'c.jpg'}}
    plan = transfer.plan_leave(files, [('client', 'HDMI-A-1', 'main'), ('server', 'DP-1', 'other')], 'client', panes=['pane'])
    assert plan.playlists == ['main', 'pane'] and plan.files == {'a.jpg', 'b.jpg'}
    assert plan.remove_playlists == {'main', 'pane'} and plan.assignments == [('HDMI-A-1', 'main')]


def test_a_pane_playlist_another_window_also_shows_stays_here():
    files = {'main': {'a.jpg'}, 'pane': {'b.jpg'}}
    plan = transfer.plan_leave(files, [('client', 'HDMI-A-1', 'main'), ('server', 'DP-1', 'pane')], 'client', panes=['pane'])
    assert plan.playlists == ['main', 'pane'] and plan.remove_playlists == {'main'}


def test_the_windows_of_a_node_are_gathered_by_screen(stores):
    set_up_window('client', 'HDMI-A-1')
    set_up_window('someone-else', 'DP-1')
    windows = bundle.collect_windows('client')
    assert list(windows) == ['HDMI-A-1']
    state = windows['HDMI-A-1']
    assert state['audio_outputs'] == 'HDMI Audio' and state['languages'] == 'de' and state['orientations'] == 3 and state['flips'] == 1
    assert state['keyboards']['enabled'] is False and state['screensavers']['idle_timeout'] == 90
    assert state['layout']['panes'] == {'2': 'pl-pane'}
    assert bundle.pane_playlists('client') == ['pl-pane']


def test_what_arrives_is_stored_under_the_new_name_with_the_new_playlist_ids(stores):
    set_up_window('client', 'HDMI-A-1')
    windows = json.loads(json.dumps(bundle.collect_windows('client')))            # as it crosses the link
    for name in bundle.WINDOW_FIELDS:
        setattr(viewer_settings, name, {})
    stores.replace_all({})
    bundle.apply_windows(windows, 'peer-7', {'pl-pane': 'new-id'})
    key = 'peer-7|HDMI-A-1'
    assert viewer_settings.audio_outputs[key] == 'HDMI Audio' and viewer_settings.languages[key] == 'de'
    assert viewer_settings.orientations[key] == 3 and viewer_settings.flips[key] == 1
    assert viewer_settings.keyboards[key].layout == 'it' and viewer_settings.screensavers[key].home_url == 'https://example.com/'
    assert stores.get(key).panes == {2: 'new-id'}


def test_a_pane_whose_playlist_did_not_arrive_is_left_empty(stores):
    set_up_window('client', 'HDMI-A-1')
    windows = json.loads(json.dumps(bundle.collect_windows('client')))
    stores.replace_all({})
    bundle.apply_windows(windows, 'peer-7', {})
    assert stores.get('peer-7|HDMI-A-1').panes == {}


def test_nothing_that_arrives_can_be_trusted(stores):
    hostile = {'HDMI-A-1': {'audio_outputs': 'x' * 400, 'languages': 'it\r\nX: y', 'orientations': 9, 'flips': True,
                            'keyboards': {'enabled': 'maybe', 'layout': '../../etc'}, 'screensavers': 'nope',
                            'layout': {'tree': 'junk', 'area': 5}},
               '../../etc/passwd': {'languages': 'de'}, 'bad name': {'languages': 'de'}, 'DP-1': 'not a dict'}
    bundle.apply_windows(hostile, 'peer-7', {})
    assert viewer_settings.audio_outputs == {} and viewer_settings.languages == {} and viewer_settings.orientations == {}
    assert viewer_settings.flips == {} and viewer_settings.keyboards == {} and viewer_settings.screensavers == {}
    assert stores.windows == {}


def test_a_failure_in_the_middle_puts_the_old_settings_back(stores, monkeypatch):
    set_up_window('peer-7', 'HDMI-A-1')
    before = dict(viewer_settings.languages)
    monkeypatch.setattr(stores, 'replace_all', lambda windows: (_ for _ in ()).throw(OSError('disk')) if windows.get('peer-7|DP-1') else None)
    with pytest.raises(OSError):
        bundle.apply_windows({'DP-1': {'languages': 'fr', 'layout': json.loads(WindowLayout().model_dump_json()), 'flips': 2}}, 'peer-7', {})
    assert viewer_settings.languages == before and 'peer-7|DP-1' not in viewer_settings.flips


def test_the_state_of_a_screen_that_arrives_replaces_what_this_node_had_for_it(stores):
    set_up_window('peer-7', 'HDMI-A-1')
    bundle.apply_windows({'HDMI-A-1': {'languages': 'fr'}}, 'peer-7', {})
    assert viewer_settings.languages == {'peer-7|HDMI-A-1': 'fr'}
    assert 'peer-7|HDMI-A-1' not in viewer_settings.audio_outputs and stores.get('peer-7|HDMI-A-1').is_default


def test_a_node_with_only_set_up_windows_is_not_empty(stores, monkeypatch):
    monkeypatch.setattr(bundle.playlists_manager, '_playlists', {}, raising=False)
    monkeypatch.setattr(bundle, 'upload_manager', type('U', (), {'files': []})())
    assert not any(a for a in [bundle.collect_windows(bundle.LOCAL_VIEWER)])
    set_up_window(bundle.LOCAL_VIEWER, 'HDMI-A-1')
    assert bundle.collect_windows(bundle.LOCAL_VIEWER)


def test_the_manifest_may_carry_windows_and_they_are_bounded():
    base = {'id': 'a' * 32, 'playlists': [], 'files': [], 'assignments': []}
    assert transfer.validate_manifest({**base, 'windows': {'HDMI-A-1': {}}})
    assert transfer.validate_manifest(base)                                   # an older node sends none
    for bad in ({'windows': []}, {'windows': {'x': 1}}, {'windows': {'': {}}}, {'windows': {str(n): {} for n in range(70)}}):
        with pytest.raises(transfer.TransferError):
            transfer.validate_manifest({**base, **bad})


def test_the_manifest_of_a_plan_carries_the_windows_and_passes_its_own_validation(stores):
    import asyncio
    set_up_window('client', 'HDMI-A-1')
    plan = transfer.Plan([], set(), [], set(), set(), bundle.collect_windows('client'))
    manifest = asyncio.run(bundle.build_manifest(plan, scope='leave:client'))
    assert manifest['windows'] == plan.windows and plan.windows['HDMI-A-1']['languages'] == 'de'
    assert transfer.validate_manifest(json.loads(json.dumps(manifest)))


def test_a_viewer_that_leaves_takes_its_settings_with_it(stores):
    from webview.controller import WebviewManager
    set_up_window('client', 'HDMI-A-1')
    set_up_window('server-side', 'DP-1')
    manager = WebviewManager.__new__(WebviewManager)
    WebviewManager.drop_viewer_settings(manager, 'client')
    assert list(viewer_settings.languages) == ['server-side|DP-1'] and list(viewer_settings.keyboards) == ['server-side|DP-1']
    assert list(viewer_settings.audio_outputs) == ['server-side|DP-1']


def test_windows_that_got_their_settings_are_told_everything_again(tmp_path, monkeypatch, stores):
    """After a node joined or left, its windows must have what came with them: the manager sends it again, as for a window that just appeared."""
    import asyncio
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from test_layout_controller import VIEWER, make_manager, settle

    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        await settle()
        ws.sent.clear()
        viewer_settings.languages = {f'{VIEWER}|HDMI-A-1': 'fr'}
        viewer_settings.keyboards = {f'{VIEWER}|HDMI-A-1': KeyboardSettings(enabled=True, layout='it')}
        mgr.windows_arrived(VIEWER)
        await settle()
        assert ws.commands('SetLanguage')[-1]['language'] == 'fr'
        assert ws.commands('SetKeyboard')[-1]['layout'] == 'it'
    asyncio.run(go())
