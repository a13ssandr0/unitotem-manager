"""Panes in the WebviewManager: what each pane of each window is told to show, across assigning, splitting, closing,
unplugging and plugging a screen back, restarting, and viewers that know nothing of panes."""
import asyncio
import json

import pytest

from utils.models.assets import Asset
from utils.models.layout import Area, LayoutError, WindowLayout, WindowLayouts, split_leaf
from webview.controller import PlaylistLoop, WebviewManager, show_payload

VIEWER = 'local-webview'
KEY = f'{VIEWER}|HDMI-A-1'


class FakeWS:
    """Stands in for the websocket manager that talks to the viewers: records every command."""

    def __init__(self):
        self.sent = []

    async def multicast(self, users, target, nocache=False, **kwargs):
        self.sent.append((users, target, kwargs))

    def commands(self, target):
        return [kw for _, t, kw in self.sent if t == target]

    def shows(self, window=None, pane=...):
        return [kw for kw in self.commands('Show')
                if (window is None or kw['window_id'] == window) and (pane is ... or kw.get('pane') == pane)]


class FakePlaylist:
    def __init__(self, playlist_id):
        self.playlist_id = playlist_id
        self.name = playlist_id
        self.current = Asset(url=f'https://{playlist_id}.test/')
        self.current_page = 0


def window(window_id, screen='HDMI-A-1'):
    return {'window_id': window_id, 'screen_name': screen, 'x': 0, 'y': 0, 'width': 1920, 'height': 1080}


def make_manager(tmp_path, monkeypatch, layout_flag=True, windows=(0,)):
    ws = FakeWS()
    mgr = WebviewManager(ws)
    mgr.layouts = WindowLayouts(tmp_path / 'window_layouts.json')
    monkeypatch.setattr(WebviewManager, '_assignments_path', staticmethod(lambda: tmp_path / 'viewer_assignments.json'))
    for pid in 'ABC':
        mgr._loops[pid] = PlaylistLoop(FakePlaylist(pid), ws)           # not started: nothing rotates in these tests
    mgr.register_webview(VIEWER, {'hostname': 'totem', 'screens': [], 'windows': []})
    info = {'screens': [], 'windows': [window(w) for w in windows]}
    if layout_flag:
        info['layout'] = True
    mgr.update_webview_info(VIEWER, info)
    return mgr, ws


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def run(coro):
    asyncio.run(coro)


def src(show):
    return show['src']


# ── a window that was never divided ──────────────────────────────────────

def test_an_undivided_window_is_one_pane_and_the_old_calls_mean_the_main_pane(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        await settle()
        # the layout goes first, then the idle page for the only pane (id 1)
        assert [t for _, t, _ in ws.sent if t in ('SetWindowLayout', 'Show')][:2] == ['SetWindowLayout', 'Show']
        layout = ws.commands('SetWindowLayout')[0]
        assert layout['tree'] == {'kind': 'leaf', 'id': 1} and layout['area']['width'] == 100
        assert ws.shows()[0]['idle'] is True and ws.shows()[0]['pane'] == 1
        ws.sent.clear()
        mgr.assign(VIEWER, 0, 'A')                                    # no pane: the main one
        await settle()
        assert [src(s) for s in ws.shows()] == ['https://A.test/'] and ws.shows()[0]['pane'] == 1
        assert mgr.get_assignment(VIEWER, 0) == 'A'
        assert json.loads((tmp_path / 'viewer_assignments.json').read_text()) == [
            {'webview_id': VIEWER, 'screen_name': 'HDMI-A-1', 'playlist_id': 'A'}]      # the old format, unchanged
    run(go())


def test_show_payload_carries_the_pane_only_when_there_is_one():
    asyncio.set_event_loop(asyncio.new_event_loop())        # an Asset arms timers
    asset = Asset(url='https://x.test/')
    assert 'pane' not in show_payload(asset, 3)
    assert show_payload(asset, 3, pane=4)['pane'] == 4


# ── dividing ──────────────────────────────────────────────────────────────

def test_a_new_pane_is_idle_and_the_old_one_is_not_disturbed(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        await settle()
        ws.sent.clear()
        new = mgr.split_pane(VIEWER, 0, 1, 'h')
        await settle()
        assert new == 2
        tree = ws.commands('SetWindowLayout')[-1]['tree']
        assert tree['kind'] == 'split' and tree['dir'] == 'h' and [tree['a']['id'], tree['b']['id']] == [1, 2]
        assert [(s['pane'], s['idle']) for s in ws.shows()] == [(2, True)]       # pane 1 was not told anything: it keeps playing
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        assert [(s['pane'], src(s)) for s in ws.shows()][-1] == (2, 'https://B.test/')
        assert mgr.get_assignment(VIEWER, 0) == 'A'                       # the window's assignment is the main pane's
        assert mgr.layouts.get(KEY).panes == {2: 'B'}
        assert mgr._loops['A'].assigned_webviews == {(VIEWER, 0, 1)}
        assert mgr._loops['B'].assigned_webviews == {(VIEWER, 0, 2)}
    run(go())


def test_the_same_playlist_may_drive_several_panes_and_a_pane_changes_playlist(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.split_pane(VIEWER, 0, 1, 'v')
        mgr.assign(VIEWER, 0, 'A', pane=2)
        mgr.assign(VIEWER, 0, 'A')
        await settle()
        assert mgr._loops['A'].assigned_webviews == {(VIEWER, 0, 1), (VIEWER, 0, 2)}
        mgr.assign(VIEWER, 0, 'C', pane=2)
        assert mgr._loops['A'].assigned_webviews == {(VIEWER, 0, 1)}
        assert mgr._loops['C'].assigned_webviews == {(VIEWER, 0, 2)}
    run(go())


def test_unassigning_a_pane_parks_it_on_the_idle_page(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        ws.sent.clear()
        mgr.unassign(VIEWER, 0, pane=2)
        await settle()
        assert [(s['pane'], s['idle']) for s in ws.shows()] == [(2, True)]
        assert mgr.layouts.get(KEY).panes == {} and not mgr._loops['B'].assigned_webviews
    run(go())


def test_unknown_panes_are_refused(tmp_path, monkeypatch):
    async def go():
        mgr, _ = make_manager(tmp_path, monkeypatch)
        with pytest.raises(LayoutError):
            mgr.assign(VIEWER, 0, 'A', pane=9)
        with pytest.raises(LayoutError):
            mgr.unassign(VIEWER, 0, pane=9)
        with pytest.raises(LayoutError):
            mgr.close_pane(VIEWER, 0, 1)            # the last one
        with pytest.raises(KeyError):
            mgr.split_pane(VIEWER, 7, 1, 'h')       # no such window
    run(go())


# ── closing ───────────────────────────────────────────────────────────────

def test_closing_a_pane_stops_it_and_leaves_the_others_alone(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        ws.sent.clear()
        mgr.close_pane(VIEWER, 0, 2)
        await settle()
        assert ws.shows() == []                                              # nobody restarted
        assert ws.commands('SetWindowLayout')[-1]['tree'] == {'kind': 'leaf', 'id': 1}
        assert not mgr._loops['B'].assigned_webviews and mgr.layouts.get(KEY).panes == {}
        assert KEY not in mgr.layouts.windows                                # a default layout is not kept
    run(go())


def test_closing_the_main_pane_promotes_the_next_and_does_not_restart_it(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        ws.sent.clear()
        mgr.close_pane(VIEWER, 0, 1)
        await settle()
        assert mgr.get_assignment(VIEWER, 0) == 'B'                         # pane 2 is the main pane now
        assert mgr._loops['B'].assigned_webviews == {(VIEWER, 0, 2)} and not mgr._loops['A'].assigned_webviews
        assert ws.shows() == []                                              # what stays on screen is not restarted
        assert mgr.layouts.get(KEY).leaf_ids == [2]
        assert json.loads((tmp_path / 'viewer_assignments.json').read_text())[0]['playlist_id'] == 'B'
        # and the window now has one pane with id 2 (ids are not renumbered)
        assert ws.commands('SetWindowLayout')[-1]['tree'] == {'kind': 'leaf', 'id': 2}
    run(go())


def test_closing_the_main_pane_when_the_next_one_has_nothing_leaves_the_window_unassigned(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        await settle()
        mgr.close_pane(VIEWER, 0, 1)
        await settle()
        assert mgr.get_assignment(VIEWER, 0) is None and not mgr._loops['A'].assigned_webviews
    run(go())


# ── area and splitter ─────────────────────────────────────────────────────

def test_area_and_ratio_are_sent_and_kept(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.set_area(VIEWER, 0, Area(left=0, top=33.33, width=100, height=33.33, margin_color='#101010'))
        mgr.set_split_ratio(VIEWER, 0, '', 0.3)
        await settle()
        last = ws.commands('SetWindowLayout')[-1]
        assert last['area'] == {'left': 0.0, 'top': 33.33, 'width': 100.0, 'height': 33.33, 'margin_color': '#101010'}
        assert last['tree']['ratio'] == 0.3
        again = WindowLayouts.load(tmp_path / 'window_layouts.json').get(KEY)
        assert again.area.top == 33.33 and again.tree.ratio == 0.3 and again.leaf_ids == [1, 2]
    run(go())


# ── restart, unplug and plug ──────────────────────────────────────────────

def test_a_restarted_manager_restores_every_pane(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.split_pane(VIEWER, 0, 2, 'v')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        mgr.assign(VIEWER, 0, 'C', pane=3)
        mgr.set_area(VIEWER, 0, Area(top=30, height=40))
        await settle()

        # a new process: the files are all it has
        again, ws2 = make_manager(tmp_path, monkeypatch, windows=())
        again.layouts = WindowLayouts.load(tmp_path / 'window_layouts.json')
        again.load_assignments()
        again.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0)], 'layout': True})
        await settle()
        tree = ws2.commands('SetWindowLayout')[-1]
        assert tree['area']['top'] == 30 and [leaf for leaf in (tree['tree']['a']['id'], tree['tree']['b']['a']['id'],
                                                               tree['tree']['b']['b']['id'])] == [1, 2, 3]
        assert {(s['pane'], src(s)) for s in ws2.shows()} == {(1, 'https://A.test/'), (2, 'https://B.test/'),
                                                              (3, 'https://C.test/')}
    run(go())


def test_a_screen_plugged_back_gets_its_layout_and_playlists_under_a_new_window_id(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        # unplugged (another screen is there, so the viewer has windows to report): the window is gone
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(1, 'DP-1')]})
        await settle()
        assert not mgr._loops['A'].assigned_webviews and not mgr._loops['B'].assigned_webviews
        ws.sent.clear()
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(5)]})  # plugged back: a NEW window id
        await settle()
        assert ws.commands('SetWindowLayout')[0]['window_id'] == 5
        assert {(s['window_id'], s['pane'], src(s)) for s in ws.shows()} == {(5, 1, 'https://A.test/'),
                                                                             (5, 2, 'https://B.test/')}
        assert mgr._loops['B'].assigned_webviews == {(VIEWER, 5, 2)}
        assert mgr.layouts.get(KEY).leaf_ids == [1, 2]
    run(go())


def test_another_screen_has_its_own_layout(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0), window(1, 'DP-1')], 'layout': True})
        mgr.split_pane(VIEWER, 0, 1, 'h')
        await settle()
        assert mgr.get_layout(VIEWER, 0).leaf_ids == [1, 2] and mgr.get_layout(VIEWER, 1).leaf_ids == [1]
    run(go())


def test_a_layout_follows_an_output_that_was_renamed(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch, windows=())
        mgr.layouts.put(f'{VIEWER}|HDMI-1', split_leaf(WindowLayout(), 1, 'h')[0])
        mgr._assignments[(VIEWER, 'HDMI-1')] = 'A'                 # saved when the screens had X server names
        mgr.update_webview_info(VIEWER, {'screens': [], 'windows': [window(0, 'HDMI-A-1')], 'layout': True})
        await settle()
        assert mgr.layouts.get(KEY).leaf_ids == [1, 2] and f'{VIEWER}|HDMI-1' not in mgr.layouts.windows
        assert mgr.get_assignment(VIEWER, 0) == 'A'
    run(go())


# ── playlists that go away, viewers that leave ────────────────────────────

def test_deleting_a_playlist_idles_the_panes_that_showed_it(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        ws.sent.clear()
        mgr.remove_playlist_loop('B')
        await settle()
        assert [(s['pane'], s['idle']) for s in ws.shows()] == [(2, True)]
        assert mgr.layouts.get(KEY).panes == {}
        ws.sent.clear()
        mgr.remove_playlist_loop('A')
        await settle()
        assert [(s['pane'], s['idle']) for s in ws.shows()] == [(1, True)]
    run(go())


def test_the_layouts_of_a_viewer_that_leaves_go_with_it(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.register_webview('client', {'hostname': 'c', 'screens': [], 'windows': []})
        mgr.update_webview_info('client', {'screens': [], 'windows': [window(0, 'HDMI-A-1')], 'layout': True})
        mgr.split_pane('client', 0, 1, 'h')
        mgr.assign('client', 0, 'C', pane=2)
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        assert sorted(mgr.pane_assignments()) == [('client', 'HDMI-A-1', 2, 'C'), (VIEWER, 'HDMI-A-1', 2, 'B')]
        mgr.drop_viewer_assignments('client')
        assert 'client|HDMI-A-1' not in mgr.layouts.windows and KEY in mgr.layouts.windows
    run(go())


def test_a_playlist_shown_in_a_pane_of_another_node_is_not_removed_when_a_client_leaves(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        from utils.remote import bundle        # builds the upload manager, which needs the node's /var/lib/unitotem
    except OSError:
        pytest.skip('/var/lib/unitotem is not writable here (runs in autopkgtest, as root)')

    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.register_webview('client', {'hostname': 'c', 'screens': [], 'windows': []})
        mgr.update_webview_info('client', {'screens': [], 'windows': [window(0, 'HDMI-A-1')], 'layout': True})
        mgr.assign('client', 0, 'B')                         # the client shows B, and nobody else does
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'A', pane=2)                   # a pane of this very node shows A
        monkeypatch.setattr(bundle, '_vm', lambda: mgr)
        monkeypatch.setattr(bundle, 'playlist_files', lambda: {'A': set(), 'B': set(), 'C': set()})
        monkeypatch.setattr(bundle.playlists_manager, '_default_id', None)
        plan = bundle.plan_for('client')
        assert plan.playlists == ['B'] and plan.remove_playlists == {'B'}
        mgr.assign('client', 0, 'A')                         # now the client shows what a pane here shows
        plan = bundle.plan_for('client')
        assert plan.playlists == ['A'] and plan.remove_playlists == set()       # kept: another node's pane uses it
    run(go())


# ── viewers that know nothing of panes ────────────────────────────────────

def test_an_old_viewer_is_driven_as_one_pane_without_layout_commands(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch, layout_flag=False)
        mgr.assign(VIEWER, 0, 'A')
        await settle()
        assert ws.commands('SetWindowLayout') == []
        assert [src(s) for s in ws.shows()][-1] == 'https://A.test/'
        mgr.split_pane(VIEWER, 0, 1, 'h')               # stored, but nothing is sent and nothing but the main pane plays
        mgr.assign(VIEWER, 0, 'B', pane=2)
        await settle()
        assert ws.commands('SetWindowLayout') == []
        assert mgr._loops['A'].assigned_webviews == {(VIEWER, 0, 1)} and not mgr._loops['B'].assigned_webviews
    run(go())


# ── what the web UI sees ──────────────────────────────────────────────────

def test_the_web_ui_gets_the_playlist_of_every_pane_the_main_one_included(tmp_path, monkeypatch):
    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        win = mgr.get_webviews()[VIEWER]['windows'][0]
        assert win['assigned_playlist'] == 'A'
        info = win['layout']
        assert info['main'] == 1 and info['panes'] == {'1': 'A', '2': 'B'}
        assert info['tree']['kind'] == 'split' and info['area']['margin_color'] == 'rgb(0,0,0)'
        assert 'next_id' not in info
    run(go())


# ── backup ────────────────────────────────────────────────────────────────

def test_layouts_are_a_backup_part_that_restores_and_resets(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())        # the upload manager built by the import wants one
    try:
        import utils.backup as backup       # builds the upload manager, which needs the node's /var/lib/unitotem
    except OSError:
        pytest.skip('/var/lib/unitotem is not writable here (runs in autopkgtest, as root)')

    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        monkeypatch.setattr(backup, 'window_layouts', mgr.layouts)
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        mgr.assign(VIEWER, 0, 'A')
        mgr.split_pane(VIEWER, 0, 1, 'h')
        mgr.assign(VIEWER, 0, 'B', pane=2)
        part = backup.parts()['layouts']
        saved = json.loads(json.dumps(part.export()))
        part.reset()
        await settle()
        assert mgr.layouts.windows == {} and not mgr._loops['B'].assigned_webviews
        ws.sent.clear()
        part.restore(saved, '1')
        await settle()
        assert mgr.layouts.get(KEY).leaf_ids == [1, 2]
        assert mgr._loops['B'].assigned_webviews == {(VIEWER, 0, 2)}             # the windows follow the restore at once
        assert ws.commands('SetWindowLayout')[-1]['tree']['kind'] == 'split'
    run(go())


# ── the API functions ─────────────────────────────────────────────────────

def test_the_api_answers_with_the_layout_or_an_error(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        import api.ws.endpoints                 # builds the API registry and the upload manager (needs /var/lib/unitotem)
        from api.viewers import Viewers
    except OSError:
        pytest.skip('/var/lib/unitotem is not writable here (runs in autopkgtest, as root)')
    from api.ws.responses import WSBroadcast, WSResponse

    async def go():
        mgr, ws = make_manager(tmp_path, monkeypatch)
        monkeypatch.setattr(WebviewManager, '_instance', mgr)
        api = Viewers(None, None)
        for name in ('layout', 'list'):                 # the registry names the functions when it is built
            getattr(Viewers, name).api_path = f'Viewers/{name}'

        reply = api.split_pane(VIEWER, 0, 1, 'h')
        assert isinstance(reply, WSBroadcast) and reply.target == 'Viewers/layout'
        assert reply.kwargs['layout']['tree']['kind'] == 'split' and reply.kwargs['layout']['panes'] == {}
        assert isinstance(api.split_pane(VIEWER, 0, 9, 'h'), WSResponse) and api.split_pane(VIEWER, 0, 9, 'h').kwargs['error']
        assert api.split_pane(VIEWER, 7, 1, 'h').kwargs['error']                        # no such window
        assert api.set_area(VIEWER, 0, margin_color='red; background:url(x)').kwargs['error']
        reply = api.set_area(VIEWER, 0, top=33.33, height=33.34, margin_color='#112233')
        assert reply.kwargs['layout']['area']['top'] == 33.33 and reply.kwargs['layout']['area']['margin_color'] == '#112233'
        assert api.set_split_ratio(VIEWER, 0, '', 0.25).kwargs['layout']['tree']['ratio'] == 0.25
        assert api.set_split_ratio(VIEWER, 0, 'a', 0.25).kwargs['error']
        reply = api.assign_playlist(VIEWER, 0, 'B', 2)
        assert reply.kwargs['viewers'][VIEWER]['windows'][0]['layout']['panes'] == {'2': 'B'}
        assert api.assign_playlist(VIEWER, 0, 'B', 9).kwargs['error']
        reply = api.close_pane(VIEWER, 0, 1)                                            # the main pane: the answer is the whole list
        assert reply.kwargs['viewers'][VIEWER]['windows'][0]['assigned_playlist'] == 'B'
        assert api.close_pane(VIEWER, 0, 2).kwargs['error']                             # the last pane
    run(go())
