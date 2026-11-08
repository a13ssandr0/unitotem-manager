"""Without cefpython3 the viewer must fall back to running headless, and the DevTools session-to-window lookup must work."""
import pytest

import utils.ready as ready
from plugins.unitotem_viewer import run_gui
from plugins.unitotem_viewer.cdp import Cdp


def test_missing_cefpython_makes_run_gui_return_false_without_raising(monkeypatch):
    import plugins.unitotem_viewer.app as viewer_app
    monkeypatch.setattr(viewer_app, 'cef', None)           # cefpython3 is not installed
    reasons = []
    monkeypatch.setattr(ready, 'ready', lambda reason='': reasons.append(reason))
    monkeypatch.setattr(viewer_app.WebviewApp, 'display_available', staticmethod(lambda: pytest.fail('no display check')))

    assert run_gui(lambda **kwargs: None) is False
    assert reasons == ['no viewer']


def test_window_of_resolves_a_page_session_and_a_frame_session():
    cdp = Cdp(port=9223, on_focus=lambda *args: None)
    cdp._sessions = {
        'page-1': {'type': 'page', 'window': 0, 'page': 'page-1'},
        'frame-1': {'type': 'iframe', 'window': None, 'page': 'page-1'},
        'page-2': {'type': 'page', 'window': None, 'page': 'page-2'},
    }
    cdp._pages = {0: 'page-1', 1: 'page-2'}                 # a frame has no window: it is found through its page
    assert cdp._window_of('page-1') == 0
    assert cdp._window_of('frame-1') == 0
    assert cdp._window_of('page-2') == 1
    assert cdp._window_of('unknown') is None
    assert cdp._window_of(None) is None
