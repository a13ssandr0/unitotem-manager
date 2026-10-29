"""Turning and mirroring a window's screen: the compositor does it, the page only when the compositor cannot."""
import asyncio
from types import SimpleNamespace

import pytest

from unitotem_node.system.screens import wayland
from unitotem_viewer.app import WebviewApp
from unitotem_viewer.window import PageCommands


class FakePage(PageCommands):
    def __init__(self):
        self.js = []
        self._cef = SimpleNamespace(execute_js=self.js.append)


def test_the_page_turns_its_content_unless_the_output_is_turned():
    page = FakePage()
    page.set_turn(orientation=5, flip=2)
    assert (page.orientation, page.flip) == (1, 2)
    page.show_turn()
    page.turned_by_output = True
    page.show_turn()
    assert page.js == ['setOrientation(1); setFlip(2)', 'setOrientation(0); setFlip(0)']


def test_flip_values_other_than_one_and_two_mean_no_flip():
    page = FakePage()
    page.set_turn(flip=1)
    page.set_turn(flip=7)
    assert page.flip == 0


@pytest.fixture
def app(monkeypatch):
    emitted = []
    app = WebviewApp.__new__(WebviewApp)
    app._bridge = SimpleNamespace(command_received=SimpleNamespace(emit=emitted.append))
    app.emitted = emitted
    app.calls = []
    app.current = 'normal'

    async def outputs():
        return [wayland.Output('HDMI-A-1', True, True, 1920, 1080, transform=app.current)]

    async def set_transform(name, transform):
        app.calls.append((name, transform))
        if transform == '180':
            raise RuntimeError('refused')
    monkeypatch.setattr(wayland, 'outputs', outputs)
    monkeypatch.setattr(wayland, 'set_transform', set_transform)
    return app


def test_the_output_is_turned_and_the_page_told(app):
    asyncio.run(app._turn_output(3, 'HDMI-A-1', 1, 0, 7))
    assert app.calls == [('HDMI-A-1', '270')]
    assert app.emitted == [{'target': '_OutputTurned', 'window_id': 3, 'ok': True, 'seq': 7}]


def test_an_output_that_already_has_the_transform_is_left_alone(app):
    app.current = 'flipped-90'
    asyncio.run(app._turn_output(3, 'HDMI-A-1', 3, 1, 1))
    assert app.calls == [] and app.emitted[0]['ok'] is True


@pytest.mark.parametrize('screen, orientation', [('HDMI-A-1', 2), ('DP-9', 1)])
def test_when_the_compositor_cannot_the_page_turns_its_content(app, screen, orientation):
    asyncio.run(app._turn_output(3, screen, orientation, 0, 1))
    assert app.emitted[0]['ok'] is False
