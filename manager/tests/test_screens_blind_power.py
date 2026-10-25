"""A monitor switched off over DDC/CEC/serial keeps its link: only what was commanded is known about it."""
import asyncio

import pytest

from unitotem_node.models.screen_control import OutputConfig, ScreenControl
from unitotem_node.system.screens import control, ddc_ctl, wayland


def _cfg(**kw):
    return OutputConfig(**kw)


@pytest.fixture
def fake_screen(monkeypatch, tmp_path):
    monkeypatch.setattr(control, 'STATE_FILE', tmp_path / 'screen_state.json')
    out = wayland.Output(name='HDMI-A-1', connected=True, active=True, width=1920, height=1080, x=0, y=0, rotation='normal')
    state = {'outputs': [out], 'calls': []}

    async def outputs():
        return state['outputs']

    async def do_power(name, mechanism, on, cfg, x):
        state['calls'].append((name, mechanism, on))

    async def tools():
        return {'ddc': (True, ''), 'cec_devices': []}

    monkeypatch.setattr(wayland, 'outputs', outputs)
    monkeypatch.setattr(control, '_do_power', do_power)
    monkeypatch.setattr(control, '_tools', tools)
    monkeypatch.setattr(ScreenControl, 'save', lambda self: None)
    control.screen_control.outputs['HDMI-A-1'] = _cfg(ddc_bus=10, ddc_features=[ddc_ctl.VCP_POWER])
    control._commanded_off.clear()
    yield state
    control._commanded_off.clear()
    control.screen_control.outputs.pop('HDMI-A-1', None)


def test_commanded_off_is_remembered_for_blind_mechanisms_only(fake_screen):
    run = asyncio.run
    assert run(control.set_power('HDMI-A-1', 'off', 'ddc')) == 'ddc'
    assert control._commanded_off['HDMI-A-1']['mechanism'] == 'ddc'
    assert run(control.screens())[0]['commanded_off']['mechanism'] == 'ddc'
    assert run(control.power_state('HDMI-A-1'))['commanded_off']['mechanism'] == 'ddc'
    run(control.set_power('HDMI-A-1', 'on', 'ddc'))
    assert 'HDMI-A-1' not in control._commanded_off
    # the compositor's own switch-off is truthful: nothing to remember
    run(control.set_power('HDMI-A-1', 'off', 'output'))
    assert 'HDMI-A-1' not in control._commanded_off


def test_commanded_off_is_forgotten_when_the_output_disappears(fake_screen):
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'ddc'))
    fake_screen['outputs'] = []                          # the monitor dropped its link: switched on by hand
    asyncio.run(control.screens())
    assert 'HDMI-A-1' not in control._commanded_off


def test_state_file_survives_a_restart(fake_screen):
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'ddc'))
    control._commanded_off.clear()
    control._commanded_off.update(control._load_commanded())
    assert control._commanded_off['HDMI-A-1']['mechanism'] == 'ddc'
    assert control._load_commanded().keys() == {'HDMI-A-1'}
    asyncio.run(control.set_power('HDMI-A-1', 'on', 'ddc'))
    assert control._load_commanded() == {}


def test_wake_all_wakes_only_what_we_put_to_standby(fake_screen):
    control.screen_control.outputs['HDMI-A-1'] = _cfg(ddc_bus=10, ddc_features=[ddc_ctl.VCP_POWER], power_on_at_start=True)
    # never switched off by us: the active link is trusted, nothing is sent
    asyncio.run(control.wake_all(budget=0, interval=0))
    assert fake_screen['calls'] == []
    # switched off over DDC (remembered): woken with DDC, whatever 'auto' would say
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'ddc'))
    fake_screen['calls'].clear()
    asyncio.run(control.wake_all(budget=0, interval=0))
    assert fake_screen['calls'] == [('HDMI-A-1', 'ddc', True)]
    assert 'HDMI-A-1' not in control._commanded_off
    # not asked to power on at start: left as it is
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'ddc'))
    fake_screen['calls'].clear()
    control.screen_control.outputs['HDMI-A-1'] = _cfg(ddc_bus=10, ddc_features=[ddc_ctl.VCP_POWER], power_on_at_start=False)
    asyncio.run(control.wake_all(budget=0, interval=0))
    assert fake_screen['calls'] == []


def test_auto_prefers_the_compositor_and_wakes_through_what_switched_off(fake_screen):
    assert asyncio.run(control.set_power('HDMI-A-1', 'off')) == 'output'
    assert fake_screen['calls'][-1] == ('HDMI-A-1', 'output', False)
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'ddc'))
    assert asyncio.run(control.set_power('HDMI-A-1', 'on')) == 'ddc'       # 'output' would "succeed" and wake nothing
    assert asyncio.run(control.set_power('HDMI-A-1', 'on')) == 'output'    # nothing remembered any more


def test_ddc_wake_reset_sequence(monkeypatch):
    sent = []

    async def setvcp(bus, code, value):
        sent.append((bus, code, value))

    async def nap(_):
        pass

    monkeypatch.setattr(ddc_ctl, 'setvcp', setvcp)
    monkeypatch.setattr(ddc_ctl.asyncio, 'sleep', nap)
    asyncio.run(ddc_ctl.set_power(10, True))
    assert sent == [(10, 'D6', 1)]
    sent.clear()
    asyncio.run(ddc_ctl.set_power(10, True, wake_reset=True))
    assert sent == [(10, 'D6', 5), (10, 'D6', 1)]      # measured: the LG Ultrafine wakes from 04 only this way
    sent.clear()
    asyncio.run(ddc_ctl.set_power(10, False, {}, wake_reset=True))
    assert sent == [(10, 'D6', 4)]                      # the reset is a wake-up only
