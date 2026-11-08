"""The turn and mirror of a window: the replies name the window and go to the client that asked; the controller publishes
the change to every page (Viewers/list, see test_layout_controller)."""
import asyncio

from api.ws.endpoints import WS  # noqa: F401  (the registry loads first, as in the app: avoids a circular import)
from api.ws.responses import WSResponse, WSBroadcast
from unitotem_node.api import display as display_module
from unitotem_node.api.display import Display


class FakeVM:
    def __init__(self):
        self.calls = []

    async def set_orientation(self, viewer_id, window_id, orientation):
        self.calls.append(('orientation', viewer_id, window_id, orientation))

    async def set_flip(self, viewer_id, window_id, flip):
        self.calls.append(('flip', viewer_id, window_id, flip))

    def get_orientation(self, viewer_id, window_id):
        return 3

    def get_flip(self, viewer_id, window_id):
        return 2


def test_turn_and_mirror_replies_name_the_window_and_are_not_broadcast(monkeypatch):
    vm = FakeVM()
    monkeypatch.setattr(display_module, '_vm', lambda: vm)
    for name in ('orientation', 'flip'):
        monkeypatch.setattr(getattr(Display, name), 'api_path', f'Settings/Display/{name}', raising=False)
    api = Display(None, None)

    async def go():
        replies = [api.set_orientation('local-webview', 2, 1), api.set_flip('local-webview', 2, 1),
                   api.orientation('local-webview', 2), api.flip('local-webview', 2)]
        await asyncio.sleep(0)
        return replies
    replies = asyncio.run(go())

    assert all(isinstance(r, WSResponse) and not isinstance(r, WSBroadcast) for r in replies)
    assert replies[0].target == 'Settings/Display/orientation' and replies[0].kwargs == {
        'viewer_id': 'local-webview', 'window_id': 2, 'orientation': 1}
    assert replies[1].target == 'Settings/Display/flip' and replies[1].kwargs == {
        'viewer_id': 'local-webview', 'window_id': 2, 'flip': 1}
    assert replies[2].kwargs == {'viewer_id': 'local-webview', 'window_id': 2, 'orientation': 3}
    assert replies[3].kwargs == {'viewer_id': 'local-webview', 'window_id': 2, 'flip': 2}
    assert ('orientation', 'local-webview', 2, 1) in vm.calls and ('flip', 'local-webview', 2, 1) in vm.calls
