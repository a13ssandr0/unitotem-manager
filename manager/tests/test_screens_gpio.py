"""A GPIO line as a screen's power switch: libgpiod is replaced by a small fake that records what the lines are told."""
import asyncio
import enum
import sys
import types

import pytest

from unitotem_node.models.screen_control import GpioConfig, OutputConfig, ScreenControl
from unitotem_node.system.screens import control, gpio, wayland


class FakeRequest:
    def __init__(self, log, config):
        self.log, self.config, self.released = log, config, False
        for line, settings in config.items():
            log.append(('request', line, settings.output_value.name, settings.active_low))

    def set_value(self, line, value):
        self.log.append(('set', line, value.name))

    def release(self):
        self.released = True
        self.log.append(('release',))


@pytest.fixture
def fake_gpiod(monkeypatch):
    log = []

    class Direction(enum.Enum):
        OUTPUT = 1

    class Value(enum.Enum):
        INACTIVE = 0
        ACTIVE = 1

    module = types.ModuleType('gpiod')
    module.request_lines = lambda path, consumer, config: (log.append(('open', path, consumer)), FakeRequest(log, config))[1]
    module.LineSettings = lambda direction, active_low, output_value: types.SimpleNamespace(
        direction=direction, active_low=active_low, output_value=output_value)
    line = types.ModuleType('gpiod.line')
    line.Direction, line.Value = Direction, Value
    monkeypatch.setitem(sys.modules, 'gpiod', module)
    monkeypatch.setitem(sys.modules, 'gpiod.line', line)
    gpio._held.clear()
    yield log
    gpio._held.clear()


def test_chip_names_are_validated():
    assert gpio.normalise_chip('gpiochip0') == 'gpiochip0'
    assert gpio.normalise_chip('/dev/gpiochip12') == 'gpiochip12'
    for bad in ('gpiochip0\n', '../gpiochip0', '/dev/gpiochip0/../x', 'gpiochip', 'sda', '-gpiochip0', ''):
        with pytest.raises(ValueError):
            gpio.normalise_chip(bad)


def test_model_limits():
    assert GpioConfig(line=17).mode == 'level'
    for bad in (dict(line=-1), dict(line=5000), dict(line=1, chip='x'), dict(line=1, mode='toggle'), dict(line=1, pulse_ms=10)):
        with pytest.raises(ValueError):
            GpioConfig(**bad)


def test_level_is_requested_at_the_value_wanted_then_driven(fake_gpiod):
    asyncio.run(gpio.set_level('gpiochip0', 17, False, False))
    asyncio.run(gpio.set_level('gpiochip0', 17, False, True))
    assert fake_gpiod == [('open', '/dev/gpiochip0', 'unitotem'), ('request', 17, 'INACTIVE', False), ('set', 17, 'ACTIVE')]


def test_active_low_is_the_librarys_business(fake_gpiod):
    asyncio.run(gpio.set_level('gpiochip1', 4, True, True))
    assert ('request', 4, 'ACTIVE', True) in fake_gpiod


def test_shutdown_gives_the_screens_power_back(fake_gpiod):
    asyncio.run(gpio.set_level('gpiochip0', 17, False, False))
    fake_gpiod.clear()
    gpio.release_all()
    assert fake_gpiod == [('set', 17, 'ACTIVE'), ('release',)]
    assert not gpio._held


def test_pulse_asserts_then_releases_the_line(fake_gpiod):
    asyncio.run(gpio.pulse('gpiochip0', 23, False, 50))
    assert fake_gpiod[1:] == [('request', 23, 'ACTIVE', False), ('set', 23, 'INACTIVE'), ('release',)]


def test_pulse_refuses_a_line_held_by_a_level_screen(fake_gpiod):
    asyncio.run(gpio.set_level('gpiochip0', 17, False, True))
    with pytest.raises(gpio.GpioError):
        asyncio.run(gpio.pulse('gpiochip0', 17, False, 50))


def test_missing_library_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, 'gpiod', None)
    with pytest.raises(gpio.GpioError, match='python3-libgpiod'):
        asyncio.run(gpio.set_level('gpiochip0', 1, False, True))
    assert gpio.availability(GpioConfig(line=1))[0] is False


# ── through the screen control facade ─────────────────────────────────────────

@pytest.fixture
def screen(monkeypatch, tmp_path, fake_gpiod):
    monkeypatch.setattr(control, 'STATE_FILE', tmp_path / 'screen_state.json')
    out = wayland.Output(name='HDMI-A-1', connected=True, active=True, width=1920, height=1080, x=0, y=0, rotation='normal')
    state = {'outputs': [out]}

    async def outputs():
        return state['outputs']

    async def tools():
        return {'ddc': (False, ''), 'cec_devices': []}

    monkeypatch.setattr(wayland, 'outputs', outputs)
    monkeypatch.setattr(control, '_tools', tools)
    monkeypatch.setattr(gpio, 'availability', lambda cfg: (cfg is not None, '' if cfg else 'no GPIO line configured'))
    monkeypatch.setattr(ScreenControl, 'save', lambda self: None)
    control._commanded_off.clear()
    yield state
    control._commanded_off.clear()
    control.screen_control.outputs.pop('HDMI-A-1', None)


def _set(**gpio_kw):
    control.screen_control.outputs['HDMI-A-1'] = OutputConfig(gpio=GpioConfig(line=17, **gpio_kw))


def test_level_off_and_on_through_the_facade(screen, fake_gpiod):
    _set()
    assert asyncio.run(control.set_power('HDMI-A-1', 'off', 'gpio')) == 'gpio'
    assert control._commanded_off['HDMI-A-1']['mechanism'] == 'gpio'      # the compositor cannot see a cut supply: only what we said is known
    assert ('request', 17, 'INACTIVE', False) in fake_gpiod
    screen['outputs'] = []                                                 # the monitor lost its power: the output is gone
    assert asyncio.run(control.set_power('HDMI-A-1', 'on', 'gpio')) == 'gpio'
    assert 'HDMI-A-1' not in control._commanded_off
    assert ('set', 17, 'ACTIVE') in fake_gpiod


def test_pulse_on_is_refused_for_a_screen_that_is_on(screen, fake_gpiod):
    _set(mode='pulse', pulse_ms=50)
    with pytest.raises(control.ScreenControlError, match='already on'):
        asyncio.run(control.set_power('HDMI-A-1', 'on', 'gpio'))
    assert not [c for c in fake_gpiod if c[0] == 'request']                # no press that would have switched it off


def test_pulse_off_then_on(screen, fake_gpiod):
    _set(mode='pulse', pulse_ms=50)
    asyncio.run(control.set_power('HDMI-A-1', 'off', 'gpio'))
    asyncio.run(control.set_power('HDMI-A-1', 'on', 'gpio'))               # we know it is off: the second press is allowed
    assert len([c for c in fake_gpiod if c[0] == 'request']) == 2


def test_gpio_is_never_picked_while_the_compositor_can_do_it(screen):
    _set()
    from unitotem_node.system.screens.mechanism import candidates
    assert candidates({'output', 'gpio'}, 'off')[0] == 'output'
    assert candidates({'gpio'}, 'on') == ['gpio']
