"""A public kiosk: the browser's shortcuts do nothing, and a page that loads again (reload, crash recovery) gets its content back."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from plugins.unitotem_viewer import keys
from plugins.unitotem_viewer.keys import CONTROL, SHIFT, ALT, Decision
from test_layout_controller import VIEWER, make_manager, run, settle


def test_developer_tools_print_save_and_navigation_keys_are_blocked():
    blocked = [(0, 0x7B), (CONTROL | SHIFT, ord('I')), (CONTROL | SHIFT, ord('J')), (CONTROL | SHIFT, ord('C')),
               (CONTROL, ord('U')), (CONTROL, ord('P')), (CONTROL, ord('S')), (CONTROL, ord('O')), (CONTROL, ord('L')),
               (CONTROL, ord('T')), (CONTROL, ord('N')), (CONTROL, ord('W')), (ALT, 0x25), (ALT, 0x27), (0, 0x70)]
    for modifiers, code in blocked:
        assert keys.decide(modifiers, code) is Decision.BLOCK, (modifiers, code)


def test_text_keys_and_editing_shortcuts_reach_the_page():
    for modifiers, code in [(0, ord('A')), (SHIFT, ord('A')), (0, 0x0D), (0, 0x08), (CONTROL, ord('C')), (CONTROL, ord('V')),
                            (CONTROL, ord('A')), (CONTROL, ord('Z')), (CONTROL | SHIFT, ord('Z')), (CONTROL, 0x25)]:
        assert keys.decide(modifiers, code) is Decision.PASS, (modifiers, code)


def test_reload_keys_go_to_the_page_shown():
    assert keys.decide(0, 0x74) is Decision.RELOAD
    assert keys.decide(CONTROL, ord('R')) is Decision.RELOAD
    assert keys.decide(CONTROL | SHIFT, ord('R')) is Decision.RELOAD


def test_a_page_that_loads_again_gets_its_playlist_and_settings_back(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        await settle()
        ws.sent.clear()
        mgr.window_reloaded(VIEWER, 0)
        await settle()
        assert [s['src'] for s in ws.shows(0)] == ['https://A.test/']
        assert ws.commands('SetWindowLayout')
    run(go())


def test_a_reloaded_idle_window_is_parked_on_the_idle_page_again(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        await settle()
        ws.sent.clear()
        mgr.window_reloaded(VIEWER, 0)
        await settle()
        assert ws.shows(0)[0]['idle'] is True
    run(go())


# ── the developer tools channel ──────────────────────────────────────────

def test_a_devtools_token_opens_only_its_page_and_only_once():
    from plugins.unitotem_node.ws_token import new_token, take_token
    page = 'A' * 32
    token = new_token('admin', 'sid1', scope=page)
    assert not take_token(token, 'admin', 'sid1')                       # no scope: the terminal and the screen cannot use it
    token = new_token('admin', 'sid1', scope=page)
    assert not take_token(token, 'admin', 'sid1', scope='B' * 32)       # another page
    token = new_token('admin', 'sid1', scope=page)
    assert not take_token(token, 'admin', 'sid2', scope=page)           # another session
    token = new_token('admin', 'sid1', scope=page)
    assert take_token(token, 'admin', 'sid1', scope=page)
    assert not take_token(token, 'admin', 'sid1', scope=page)           # spent


def test_a_terminal_token_does_not_open_the_developer_tools():
    from plugins.unitotem_node.ws_token import new_token, take_token
    token = new_token('admin', 'sid1')
    assert not take_token(token, 'admin', 'sid1', scope='A' * 32)


def test_only_files_of_the_front_end_are_served():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())        # importing the login router arms a lock; earlier tests closed the loop
    from plugins.unitotem_node.devtools import _FILE
    for good in ('inspector.html', 'front_end/core/common/common.js', 'a-b_c/d.e'):
        assert _FILE.fullmatch(good), good
    for bad in ('../etc/passwd', 'a/../b', 'a b', 'a\n', 'a?x=1', '', 'a%2e%2e/b'):
        assert not _FILE.fullmatch(bad), bad


def test_the_targets_list_and_the_protocol_are_not_files():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from plugins.unitotem_node.devtools import _NOT_FILES
    assert {'json', 'page', 'browser', 'ws'} <= set(_NOT_FILES)


# ── the profile's preferences ────────────────────────────────────────────

def test_kiosk_preferences_are_merged_into_the_profile_and_the_rest_is_kept(tmp_path):
    import json
    from plugins.unitotem_viewer import profile_prefs
    prefs = tmp_path / 'Default' / 'Preferences'
    prefs.parent.mkdir()
    prefs.write_text(json.dumps({'profile': {'name': 'x', 'default_content_setting_values': {'images': 1}}, 'kept': 1,
                                 'autofill': {'profile_enabled': True, 'other': 5}}))
    assert profile_prefs.apply(tmp_path) is True
    got = json.loads(prefs.read_text())
    assert got['kept'] == 1 and got['profile']['name'] == 'x' and got['autofill']['other'] == 5
    assert got['autofill']['profile_enabled'] is False and got['credentials_enable_service'] is False
    assert got['profile']['default_content_setting_values'] == {'images': 1, 'notifications': 2, 'geolocation': 2, 'popups': 2}
    assert got['translate']['enabled'] is False
    assert profile_prefs.apply(tmp_path) is False                     # nothing left to change


def test_a_fresh_profile_gets_the_preferences_and_a_broken_file_is_left_alone(tmp_path):
    import json
    from plugins.unitotem_viewer import profile_prefs
    assert profile_prefs.apply(tmp_path) is True
    assert json.loads((tmp_path / 'Default' / 'Preferences').read_text())['credentials_enable_service'] is False
    broken = tmp_path / 'other'
    (broken / 'Default').mkdir(parents=True)
    (broken / 'Default' / 'Preferences').write_text('{not json')
    assert profile_prefs.apply(broken) is False
    assert (broken / 'Default' / 'Preferences').read_text() == '{not json'
