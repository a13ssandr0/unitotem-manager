"""Output modes: wlr-randr's listing and `--mode` (system/screens/wayland.py) and the API that confirms or reverts them (api/display_outputs.py).

No compositor and no tool runs: `wlr-randr` is a fake that keeps the modes and answers the same command lines."""
import asyncio
import copy
import json
import re
from types import SimpleNamespace

import pytest

from unitotem_node.api import display_outputs
from unitotem_node.system.screens import wayland
from api.ws.responses import WSResponse
from utils.models.viewer import ViewerSettings, viewer_settings

LISTING = [
    {'name': 'DP-1', 'make': 'GSM', 'model': 'LG', 'serial': '1', 'enabled': True,
     'modes': [{'width': 1920, 'height': 1080, 'refresh': 60.0, 'preferred': True, 'current': True},
               {'width': 1920, 'height': 1080, 'refresh': 59.94, 'preferred': False, 'current': False},
               {'width': 1280, 'height': 720, 'refresh': 60.0, 'preferred': False, 'current': False}],
     'position': {'x': 0, 'y': 0}, 'transform': 'normal', 'scale': 1.0},
    {'name': 'HDMI-A-1', 'enabled': False,
     'modes': [{'width': 1024, 'height': 768, 'refresh': 60.0, 'preferred': True, 'current': False}],
     'position': {'x': 0, 'y': 0}, 'transform': 'normal', 'scale': 1.0},
]


def test_modes_are_listed_with_the_refresh_in_exact_mhz():
    outs = {o.name: o for o in wayland.parse_outputs(json.dumps(LISTING))}
    dp = outs['DP-1']
    assert [(m.width, m.height, m.refresh_mhz) for m in dp.modes] == [(1920, 1080, 60000), (1920, 1080, 59940), (1280, 720, 60000)]
    assert dp.modes[0].preferred and dp.modes[0].current and not dp.modes[1].current
    assert dp.current_mode == dp.modes[0]
    assert outs['HDMI-A-1'].current_mode is None and not outs['HDMI-A-1'].enabled


def test_a_mode_without_a_refresh_rate_is_not_listed():
    text = json.dumps([{'name': 'DP-1', 'enabled': True, 'position': {}, 'transform': 'normal',
                        'modes': [{'width': 1920, 'height': 1080, 'current': True},
                                  {'width': 1280, 'height': 720, 'refresh': 60.0}]}])
    out = wayland.parse_outputs(text)[0]
    assert out.enabled and [(m.width, m.height) for m in out.modes] == [(1280, 720)]


def test_mode_argument_writes_the_rate_in_thousandths():
    assert wayland.mode_argument(1920, 1080, 59940) == '1920x1080@59.940Hz'
    assert wayland.mode_argument(800, 600, 60000) == '800x600@60.000Hz'
    assert wayland.mode_argument(640, 480, 5) == '640x480@0.005Hz'


class FakeCompositor:
    """wlr-randr with the modes of LISTING: `--json`, `--output X --mode M`, `--preferred`, `--transform`. wlopm is absent."""

    def __init__(self):
        self.outputs = copy.deepcopy(LISTING)
        self.commands = []

    def current(self, name):
        entry = next(e for e in self.outputs if e['name'] == name)
        return next(((m['width'], m['height'], round(m['refresh'] * 1000)) for m in entry['modes'] if m['current']), None)

    async def run(self, cmd, timeout=None):
        self.commands.append(cmd)
        if cmd == ['wlr-randr', '--json']:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.outputs), stderr='')
        if cmd[0] == 'wlopm':
            return SimpleNamespace(returncode=1, stdout='', stderr='no power protocol')
        entry = next((e for e in self.outputs if e['name'] == cmd[cmd.index('--output') + 1]), None)
        if entry is None:
            return SimpleNamespace(returncode=1, stdout='', stderr='no such output')
        for option, value in zip(cmd, cmd[1:]):
            if option == '--mode':
                match = [m for m in entry['modes'] if wayland.mode_argument(m['width'], m['height'], round(m['refresh'] * 1000)) == value]
                if not match:
                    return SimpleNamespace(returncode=1, stdout='', stderr=f'unknown mode: {value}')
                for m in entry['modes']:
                    m['current'] = m is match[0]
            if option == '--transform':
                entry['transform'] = value
        if '--preferred' in cmd:
            for m in entry['modes']:
                m['current'] = m['preferred']
            if not any(m['preferred'] for m in entry['modes']):
                return SimpleNamespace(returncode=1, stdout='', stderr='no preferred mode found')
        return SimpleNamespace(returncode=0, stdout='', stderr='')


@pytest.fixture
def compositor(monkeypatch):
    comp = FakeCompositor()
    monkeypatch.setattr(wayland, 'run', comp.run)
    return comp


def test_set_mode_applies_only_a_listed_mode(compositor):
    asyncio.run(wayland.set_mode('DP-1', 1920, 1080, 59940))
    assert compositor.current('DP-1') == (1920, 1080, 59940)
    assert compositor.commands[-1] == ['wlr-randr', '--output', 'DP-1', '--mode', '1920x1080@59.940Hz']


@pytest.mark.parametrize('args', [(1024, 768, 60000), (1920, 1080, 60001), ('1920', 1080, 60000), (True, 1080, 60000)])
def test_set_mode_refuses_what_is_not_listed_before_wlr_randr_runs(compositor, args):
    with pytest.raises(ValueError):
        asyncio.run(wayland.set_mode('DP-1', *args))
    assert not any('--mode' in c for c in compositor.commands)


@pytest.mark.parametrize('name', ['-x', 'DP 1', '', 'DP-1;rm'])
def test_set_mode_refuses_a_bad_output_name(compositor, name):
    with pytest.raises(ValueError):
        asyncio.run(wayland.set_mode(name, 1920, 1080, 60000))
    assert compositor.commands == []


def test_reset_mode_goes_to_the_preferred_mode_and_normal_transform(compositor):
    asyncio.run(wayland.set_mode('DP-1', 1280, 720, 60000))
    asyncio.run(wayland.reset_mode('DP-1'))
    assert compositor.current('DP-1') == (1920, 1080, 60000)
    assert compositor.commands[-1] == ['wlr-randr', '--output', 'DP-1', '--preferred', '--transform', 'normal']


# ── API ───────────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast(self, target, **kwargs):
        self.sent.append((target, kwargs))


@pytest.fixture
def api(monkeypatch, compositor, tmp_path):
    settings_file = tmp_path / 'viewer_settings.json'
    monkeypatch.setattr(ViewerSettings, '_filepath', staticmethod(lambda: settings_file))
    saved = (viewer_settings.modes, viewer_settings.orientations, viewer_settings.flips)
    viewer_settings.modes, viewer_settings.orientations, viewer_settings.flips = {}, {}, {}
    monkeypatch.setattr(display_outputs, 'REVERT_AFTER', 0.05)
    display_outputs._pending.clear()
    yield SimpleNamespace(obj=display_outputs.Outputs(FakeWS(), None), file=settings_file, comp=compositor)
    for pending in display_outputs._pending.values():
        pending.task.cancel()
    display_outputs._pending.clear()
    viewer_settings.modes, viewer_settings.orientations, viewer_settings.flips = saved


def test_outputs_describes_modes_current_saved_and_turn(api):
    viewer_settings.orientations['local-webview|DP-1'] = 1
    result = asyncio.run(api.obj.outputs())
    dp = next(o for o in result.kwargs['outputs'] if o['name'] == 'DP-1')
    assert dp['current'] == {'width': 1920, 'height': 1080, 'refresh_mhz': 60000}
    assert dp['saved'] is None and dp['pending'] is None and dp['orientation'] == 1 and dp['flip'] == 0
    assert {'width': 1920, 'height': 1080, 'refresh_mhz': 59940, 'preferred': False, 'current': False} in dp['modes']


def test_set_mode_is_pending_until_confirmed_and_is_saved_only_then(api):
    async def scenario():
        reply = await api.obj.set_mode('DP-1', 1920, 1080, 59940)
        assert 'outputs' in reply.kwargs
        assert api.comp.current('DP-1') == (1920, 1080, 59940)
        assert reply.kwargs['outputs'][0]['pending']['refresh_mhz'] == 59940
        assert not api.file.exists() and viewer_settings.modes == {}     # nothing written before the confirmation
        await api.obj.confirm_mode('DP-1')
        await asyncio.sleep(0.15)                                       # past REVERT_AFTER: the timer was cancelled
        return api.comp.current('DP-1')

    assert asyncio.run(scenario()) == (1920, 1080, 59940)
    saved = json.loads(api.file.read_text())
    assert saved['modes'] == {'local-webview|DP-1': {'width': 1920, 'height': 1080, 'refresh_mhz': 59940}}


def test_set_mode_reverts_by_itself_after_the_timeout(api):
    async def scenario():
        await api.obj.set_mode('DP-1', 1280, 720, 60000)
        await asyncio.sleep(0.2)
        return api.comp.current('DP-1'), display_outputs._pending, api.obj.ws.sent

    current, pending, sent = asyncio.run(scenario())
    assert current == (1920, 1080, 60000) and pending == {}
    assert sent and sent[-1][0] == 'Settings/Display/Outputs/outputs'
    assert not api.file.exists()


def test_a_second_change_reverts_to_the_mode_before_the_first(api):
    async def scenario():
        await api.obj.set_mode('DP-1', 1280, 720, 60000)
        await api.obj.set_mode('DP-1', 1920, 1080, 59940)
        await asyncio.sleep(0.2)
        return api.comp.current('DP-1')

    assert asyncio.run(scenario()) == (1920, 1080, 60000)


def test_an_invalid_mode_is_refused_and_changes_nothing(api):
    async def scenario():
        reply = await api.obj.set_mode('DP-1', 1024, 768, 60000)
        return reply, display_outputs._pending

    reply, pending = asyncio.run(scenario())
    assert isinstance(reply, WSResponse) and reply.kwargs.get('error')
    assert pending == {} and api.comp.current('DP-1') == (1920, 1080, 60000)


def test_a_mode_on_an_output_that_is_off_is_refused(api):
    reply = asyncio.run(api.obj.set_mode('HDMI-A-1', 1024, 768, 60000))
    assert isinstance(reply, WSResponse) and 'no current mode' in reply.kwargs['error']


def test_confirm_without_a_pending_change_is_an_error(api):
    reply = asyncio.run(api.obj.confirm_mode('DP-1'))
    assert isinstance(reply, WSResponse) and reply.kwargs.get('error')
    assert not api.file.exists()


def test_reset_output_drops_the_saved_mode_turn_and_mirror(api):
    async def scenario():
        viewer_settings.orientations['local-webview|DP-1'] = 2
        viewer_settings.flips['local-webview|DP-1'] = 1
        await api.obj.set_mode('DP-1', 1280, 720, 60000)
        await api.obj.confirm_mode('DP-1')
        await api.obj.set_mode('DP-1', 1280, 720, 60000)         # pending again: reset cancels its timer
        reply = await api.obj.reset_output('DP-1')
        await asyncio.sleep(0.15)
        return reply, api.comp.current('DP-1')

    reply, current = asyncio.run(scenario())
    assert current == (1920, 1080, 60000)                        # preferred, and not reverted to 1280x720 later
    dp = next(o for o in reply.kwargs['outputs'] if o['name'] == 'DP-1')
    assert dp['saved'] is None and dp['pending'] is None and dp['orientation'] == 0 and dp['flip'] == 0
    assert viewer_settings.modes == {} and viewer_settings.orientations == {} and viewer_settings.flips == {}
    assert json.loads(api.file.read_text())['modes'] == {}
    assert ['wlr-randr', '--output', 'DP-1', '--preferred', '--transform', 'normal'] in api.comp.commands


def test_a_bad_output_name_is_refused_before_the_compositor_is_asked(api):
    reply = asyncio.run(api.obj.set_mode('DP 1', 1920, 1080, 60000))
    assert isinstance(reply, WSResponse) and reply.kwargs.get('error')
    assert api.comp.commands == []


def test_saved_modes_that_do_not_validate_are_dropped_and_the_rest_kept(tmp_path, monkeypatch):
    path = tmp_path / 'viewer_settings.json'
    path.write_text(json.dumps({'modes': {'local-webview|DP-1': {'width': 1920, 'height': 1080, 'refresh_mhz': 60000},
                                          'local-webview|DP-2': {'width': 0, 'height': 1080, 'refresh_mhz': 60000},
                                          'local-webview|DP-3': 'junk'}}))
    monkeypatch.setattr(ViewerSettings, '_filepath', staticmethod(lambda: path))
    loaded = ViewerSettings.load()
    assert set(loaded.modes) == {'local-webview|DP-1'}
