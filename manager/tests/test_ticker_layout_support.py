"""The bar of a window is set only for a viewer that supports layouts (docs/manager/layout-and-ticker.md: `SetTicker` goes to
layout-aware viewers only). `Ticker.set_settings` must follow the same rule as the pushes in utils/ticker/push.py."""
import asyncio

import pytest

import api.ws.endpoints  # noqa: F401  (the endpoint registry imports api.ticker: load it first, as the app does)
import api.ticker as ticker_api
import utils.ticker as ticker
from api.ticker import Ticker
from utils.models.layout import WindowLayouts
from utils.models.ticker import TickerSettings
from utils.ticker.store import TickerStore
from utils.ticker.windows import WindowTickers
from webview.controller import WebviewManager

VIEWER = 'local-webview'
SCREEN = 'HDMI-A-1'


class FakeWS:
    """Stands in for the websocket manager that talks to the viewers: records every command."""

    def __init__(self):
        self.sent = []

    async def multicast(self, users, target, nocache=False, **kwargs):
        self.sent.append((users, target, kwargs))

    def targets(self):
        return [t for _, t, _ in self.sent]


@pytest.fixture
def ticker_state(tmp_path, monkeypatch):
    layouts = WindowLayouts(tmp_path / 'window_layouts.json')
    monkeypatch.setattr(ticker, '_windows', WindowTickers(layouts))
    monkeypatch.setattr(ticker, '_store', TickerStore(tmp_path / 'sources.json', tmp_path / 'cache.json'))
    return layouts


def make_viewer(tmp_path, monkeypatch, layout: bool):
    ws = FakeWS()
    mgr = WebviewManager(ws)
    mgr.layouts = WindowLayouts(tmp_path / 'window_layouts.json')
    monkeypatch.setattr(WebviewManager, '_assignments_path', staticmethod(lambda: tmp_path / 'viewer_assignments.json'))
    mgr.register_webview(VIEWER, {'hostname': 'totem', 'screens': [], 'windows': []})
    info = {'screens': [], 'windows': [{'window_id': 0, 'screen_name': SCREEN, 'x': 0, 'y': 0, 'width': 1920, 'height': 1080}]}
    if layout:
        info['layout'] = True
    mgr.update_webview_info(VIEWER, info)
    monkeypatch.setattr(ticker_api, '_vm', lambda: mgr)
    return ws


async def bar_off_to(tmp_path, monkeypatch, layout: bool) -> FakeWS:
    """Sets an enabled bar on the window of a viewer (with or without layout support) and returns what the viewer got."""
    ws = make_viewer(tmp_path, monkeypatch, layout=layout)
    api = Ticker(None, None)
    await api.set_settings(VIEWER, 0, TickerSettings(enabled=True).model_dump())
    return ws


def test_viewer_with_layout_support_is_told_the_bar(tmp_path, monkeypatch, ticker_state):
    # control case: the same call reaches a layout-aware viewer
    ws = asyncio.run(bar_off_to(tmp_path, monkeypatch, layout=True))
    assert 'SetTicker' in ws.targets()


def test_viewer_without_layout_support_is_not_told_the_bar(tmp_path, monkeypatch, ticker_state):
    # an older viewer has no panes and no layout messages: no bar message may reach it
    ws = asyncio.run(bar_off_to(tmp_path, monkeypatch, layout=False))
    assert 'SetTicker' not in ws.targets()
    assert 'TickerItems' not in ws.targets()
