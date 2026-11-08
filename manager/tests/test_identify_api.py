"""Settings/Display/Screens/identify: the node asks its own viewer to show a screen's name; the same permission as the other screen calls."""
import asyncio

import pytest

from api.ws.responses import WSResponse
from unitotem_node.api import display_identify
from unitotem_node.api.display_identify import LOCAL_VIEWER, IdentifyScreen
from unitotem_node.api.display_screens import Screens
from utils.models.user import UserPerms


class FakeManager:
    def __init__(self, names):
        self.names = names
        self.sent = []

    @classmethod
    def get_instance(cls):
        return cls.instance

    def get_webview_screens(self, webview_id):
        assert webview_id == LOCAL_VIEWER
        return [{'index': i, 'name': name} for i, name in enumerate(self.names)]

    async def send_webview_command(self, webview_id, target, **kwargs):
        self.sent.append((webview_id, target, kwargs))


@pytest.fixture
def manager(monkeypatch):
    fake = FakeManager(['HDMI-A-1', 'DP-2'])
    monkeypatch.setattr(FakeManager, 'instance', fake, raising=False)
    monkeypatch.setattr(display_identify, 'WebviewManager', FakeManager)
    return fake


def run(name=None):
    return asyncio.run(IdentifyScreen().identify(name))


def test_one_screen_or_every_screen(manager):
    assert run('DP-2') is None
    assert run() is None
    assert manager.sent == [(LOCAL_VIEWER, 'Identify', {'screen_name': 'DP-2'}),
                            (LOCAL_VIEWER, 'Identify', {'screen_name': None})]


def test_an_unknown_or_malformed_name_is_an_error_and_sends_nothing(manager):
    for name in ['HDMI-A-9', '../x', 'DP 2', '', 'a' * 65]:
        response = run(name)
        assert isinstance(response, WSResponse), name
        assert 'error' in response.kwargs, name
    assert manager.sent == []


def test_the_path_is_the_screens_path_with_the_power_permission():
    assert Screens.identify is IdentifyScreen.identify
    assert UserPerms.power in IdentifyScreen.identify.perms
