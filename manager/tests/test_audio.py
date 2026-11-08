"""System audio (PulseAudio through pulsectl) and its websocket API: sink listing, default sink, volume and mute.
Pulse is replaced by a fake: no sound server is contacted."""
import asyncio
import math
from types import SimpleNamespace

import pytest
from pulsectl import PulseError

from api.ws.responses import WSBroadcast
from unitotem_node.api import audio as audio_api
from unitotem_node.system import audio as system_audio


class FakePulse:
    sinks = []
    default_sink = 'alsa_output.hdmi'
    error = None                       # raised by every call when set (no sound server)
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        if FakePulse.error is not None:
            raise FakePulse.error
        return self

    def __exit__(self, *exc):
        return False

    def sink_list(self):
        return list(FakePulse.sinks)

    def server_info(self):
        return SimpleNamespace(default_sink_name=FakePulse.default_sink)

    def get_sink_by_name(self, name):
        for sink in FakePulse.sinks:
            if sink.name == name:
                return sink
        raise LookupError(name)

    def default_set(self, sink):
        FakePulse.calls.append(('default_set', sink.name))
        FakePulse.default_sink = sink.name

    def volume_set_all_chans(self, sink, volume):
        FakePulse.calls.append(('volume', sink.name, volume))

    def mute(self, sink, mute):
        FakePulse.calls.append(('mute', sink.name, mute))


def sink(name, description, volume, mute):
    return SimpleNamespace(name=name, description=description, mute=mute, volume=SimpleNamespace(value_flat=volume))


@pytest.fixture
def pulse(monkeypatch):
    FakePulse.sinks = [sink('alsa_output.hdmi', 'HDMI', 0.5, 0), sink('alsa_output.usb', 'USB DAC', 1.0, 1)]
    FakePulse.default_sink = 'alsa_output.hdmi'
    FakePulse.error = None
    FakePulse.calls = []
    monkeypatch.setattr(system_audio, 'Pulse', FakePulse)
    monkeypatch.setattr(audio_api, 'get_audio_devices', system_audio.get_audio_devices)
    monkeypatch.setattr(audio_api, 'get_default_audio_device', system_audio.get_default_audio_device)
    monkeypatch.setattr(audio_api, 'set_default_audio_device', system_audio.set_default_audio_device)
    monkeypatch.setattr(audio_api, 'set_volume', system_audio.set_volume)
    monkeypatch.setattr(audio_api, 'set_mute', system_audio.set_mute)
    return FakePulse


def api():
    return audio_api.Audio(None, None)


# ── system layer ──────────────────────────────────────────────────────────

def test_sinks_are_listed_with_name_description_mute_and_volume(pulse):
    devices = system_audio.get_audio_devices()
    assert devices == [
        {'name': 'alsa_output.hdmi', 'description': 'HDMI', 'muted': False, 'volume': 0.5},
        {'name': 'alsa_output.usb', 'description': 'USB DAC', 'muted': True, 'volume': 1.0},
    ]


def test_mute_is_a_bool_whatever_pulse_returns(pulse):
    pulse.sinks = [sink('a', 'A', 0.3, 1), sink('b', 'B', 0.3, 0)]
    assert [d['muted'] for d in system_audio.get_audio_devices()] == [True, False]
    assert all(type(d['muted']) is bool for d in system_audio.get_audio_devices())


def test_default_device_is_the_server_default_sink(pulse):
    assert system_audio.get_default_audio_device() == 'alsa_output.hdmi'


def test_setting_the_default_device_selects_that_sink(pulse):
    system_audio.set_default_audio_device('alsa_output.usb')
    assert pulse.calls == [('default_set', 'alsa_output.usb')]


def test_an_unknown_default_device_is_an_error_and_changes_nothing(pulse):
    with pytest.raises(LookupError):
        system_audio.set_default_audio_device('no-such-sink')
    assert pulse.calls == []


def test_volume_and_mute_without_a_device_use_the_default_sink(pulse):
    system_audio.set_volume(None, 0.25)
    system_audio.set_mute(None, True)
    assert pulse.calls == [('volume', 'alsa_output.hdmi', 0.25), ('mute', 'alsa_output.hdmi', True)]


def test_volume_and_mute_on_a_named_device(pulse):
    system_audio.set_volume('alsa_output.usb', 0.8)
    system_audio.set_mute('alsa_output.usb', False)
    assert pulse.calls == [('volume', 'alsa_output.usb', 0.8), ('mute', 'alsa_output.usb', False)]


def test_no_sound_server_raises_pulse_error(pulse):
    pulse.error = PulseError('connection refused')
    with pytest.raises(PulseError):
        system_audio.get_audio_devices()


# ── websocket API ─────────────────────────────────────────────────────────

def test_devices_reply_carries_the_list_and_the_default(pulse):
    reply = api().devices()
    assert isinstance(reply, WSBroadcast)
    assert reply.kwargs['available'] is True
    assert reply.kwargs['default'] == 'alsa_output.hdmi'
    assert [d['name'] for d in reply.kwargs['devices']] == ['alsa_output.hdmi', 'alsa_output.usb']


def test_without_a_sound_server_the_list_is_empty_and_not_an_error(pulse):
    pulse.error = PulseError('connection refused')
    reply = api().devices()
    assert isinstance(reply, WSBroadcast)
    assert reply.kwargs['devices'] == []
    assert reply.kwargs['default'] is None
    assert reply.kwargs['available'] is False


def test_set_volume_passes_the_value_through_for_a_sensible_level(pulse):
    api().set_volume(0.4)
    assert pulse.calls == [('volume', 'alsa_output.hdmi', 0.4)]


@pytest.mark.parametrize('volume', [-0.5, 1.7, 1000.0, math.inf, -math.inf, math.nan])
def test_volume_outside_zero_to_one_is_clamped_or_refused(pulse, volume):
    """Sink volume is a 0.0-1.0 level (the scheduler uses the same range). A client must not push anything else to Pulse."""
    try:
        api().set_volume(volume)
    except (ValueError, TypeError):
        assert pulse.calls == []                       # refused: nothing sent
        return
    assert len(pulse.calls) == 1
    sent = pulse.calls[0][2]
    assert math.isfinite(sent) and 0.0 <= sent <= 1.0


def test_set_volume_on_a_named_device(pulse):
    api().set_volume(0.6, 'alsa_output.usb')
    assert pulse.calls == [('volume', 'alsa_output.usb', 0.6)]


def test_set_mute_and_set_default_reply_with_the_device_list(pulse):
    reply = api().set_mute(True)
    assert pulse.calls == [('mute', 'alsa_output.hdmi', True)]
    assert isinstance(reply, WSBroadcast) and reply.kwargs['available'] is True

    reply = api().set_default_device('alsa_output.usb')
    assert pulse.calls[-1] == ('default_set', 'alsa_output.usb')
    assert reply.kwargs['default'] == 'alsa_output.usb'

