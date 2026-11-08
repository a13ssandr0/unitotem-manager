"""Identify and the screen labels of the viewer's windows: which windows are told, what the page is sent, and the idle pages
that are shown again when the screens change. No CEF, no compositor: the page is a fake that records its calls."""
from types import SimpleNamespace

from unitotem_viewer.app import WebviewApp
from unitotem_viewer.window import IDENTIFY_MS, PageCommands

IDLE_SRC = 'https://localhost/unitotem-no-assets'


class FakeScreen:
    def __init__(self, name: str):
        self._name = name

    def name(self):
        return self._name

    def geometry(self):
        return SimpleNamespace(isEmpty=lambda: False)


class FakeWindow(PageCommands):
    def __init__(self, screen: FakeScreen):
        self.screen = screen
        self.js = []
        self._cef = SimpleNamespace(execute_js=self.js.append)
        self._idle_shows = {}


def idle(window, pane=None):
    window.show_asset(IDLE_SRC, -1, 0, None, idle=True, pane=pane)


def app_with(windows: dict, screens: list):
    app = WebviewApp.__new__(WebviewApp)
    app.windows = windows
    app.qt_app = SimpleNamespace(screens=lambda: screens)
    return app


def test_identify_is_sent_as_one_call_with_its_duration():
    win = FakeWindow(FakeScreen('HDMI-A-1'))
    win.identify('HDMI-A-1', 'Screen 2')
    assert win.js == [f'identify("HDMI-A-1", "Screen 2", {IDENTIFY_MS})']
    assert IDENTIFY_MS == 5000


def test_an_idle_page_of_one_screen_is_asked_for_without_any_query():
    win = FakeWindow(FakeScreen('HDMI-A-1'))
    idle(win)
    assert win.js == ['show("https://localhost/unitotem-no-assets", null, "contain", null, 1.0, false, 1.0, false, true, null)']


def test_an_idle_page_carries_its_screen_when_one_is_set():
    win = FakeWindow(FakeScreen('DP-2'))
    win.set_screen_label(2, 'DP-2')
    idle(win)
    assert win.js[-1].startswith('show("https://localhost/unitotem-no-assets?screen=DP-2&index=2", ')


def test_other_sources_are_never_given_a_query():
    win = FakeWindow(FakeScreen('DP-2'))
    win.set_screen_label(2, 'DP-2')
    win.show_asset('https://example.com/page', -1, 0, None, idle=True)
    assert 'example.com/page"' in win.js[-1] and '?screen' not in win.js[-1]


def test_a_label_change_shows_the_idle_pages_again_and_only_those():
    win = FakeWindow(FakeScreen('DP-2'))
    idle(win)
    idle(win, pane=3)
    before = len(win.js)
    win.set_screen_label(2, 'DP-2')
    assert len(win.js) == before + 2
    assert all('?screen=DP-2&index=2' in js for js in win.js[before:])
    win.set_screen_label(2, 'DP-2')                  # unchanged: nothing is sent
    assert len(win.js) == before + 2
    win.set_screen_label(None)                      # back to one screen: the plain address again
    assert all('?screen' not in js for js in win.js[before + 2:]) and len(win.js) == before + 4


def test_content_replaces_the_idle_page_so_it_is_not_shown_again():
    win = FakeWindow(FakeScreen('DP-2'))
    idle(win)
    win.show_asset('https://localhost/uploaded/a.png', 2, 0, None, pane=None)
    before = len(win.js)
    win.set_screen_label(2, 'DP-2')
    assert len(win.js) == before


def test_labels_are_given_only_while_several_screens_are_connected():
    left, right = FakeScreen('HDMI-A-1'), FakeScreen('DP-2')
    one = FakeWindow(left)
    windows = {1: one}
    app = app_with(windows, [left])
    app._update_screen_labels()
    assert one.screen_label is None
    app = app_with({1: one, 2: FakeWindow(right)}, [left, right])
    app._update_screen_labels()
    assert app.windows[1].screen_label == (1, 'HDMI-A-1')
    assert app.windows[2].screen_label == (2, 'DP-2')


def test_identify_one_output_or_every_window():
    left, right = FakeScreen('HDMI-A-1'), FakeScreen('DP-2')
    windows = {1: FakeWindow(left), 2: FakeWindow(right)}
    app = app_with(windows, [left, right])
    app._identify('DP-2')
    assert windows[1].js == [] and windows[2].js == [f'identify("DP-2", "Screen 2", {IDENTIFY_MS})']
    app._identify('NOT-A-SCREEN')
    assert windows[1].js == [] and len(windows[2].js) == 1
    app._identify(None)
    assert windows[1].js == [f'identify("HDMI-A-1", "Screen 1", {IDENTIFY_MS})']
    assert len(windows[2].js) == 2


def test_the_identify_command_reaches_the_handler_without_a_window():
    app = app_with({}, [])
    seen = []
    app._identify = seen.append
    app._handle_command({'target': 'Identify', 'screen_name': 'HDMI-A-1'})
    app._handle_command({'target': 'Identify'})
    assert seen == ['HDMI-A-1', None]
