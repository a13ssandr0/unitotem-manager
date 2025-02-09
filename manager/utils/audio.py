__all__ = ["Audio"]

from typing import Optional

from pulsectl import Pulse

from utils.ws.responses import WSBroadcast
from utils.ws.wsmanager import WSAPIBase


def get_audio_devices() -> list[dict[str, str | bool | float]]:
    with Pulse() as pulse:
        default_dev = pulse.server_info().default_sink_name
        return [{'name': sink.name, 'description': sink.description, 'mute': bool(sink.mute),
                 'volume': sink.volume.value_flat, 'default': sink.name == default_dev} for sink in pulse.sink_list()]


def get_default_audio_device() -> str:
    with Pulse() as pulse:
        return pulse.server_info().default_sink_name


def set_default_audio_device(dev: str):
    with Pulse() as pulse:
        pulse.default_set(pulse.get_sink_by_name(dev))


def set_volume(dev: str | None, volume: float):
    with Pulse() as pulse:
        pulse.volume_set_all_chans(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), volume)


def set_mute(dev: str | None, mute: bool):
    with Pulse() as pulse:
        pulse.mute(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), mute)


class Audio(WSAPIBase):
    def devices(self):
        return WSBroadcast(self.devices, devices=get_audio_devices())

    def default(self, device: Optional[str] = None):
        if device is not None:
            set_default_audio_device(device)
        return self.devices()

    def volume(self, device: Optional[str] = None, volume: Optional[float] = None):
        if volume is not None:
            set_volume(device, volume)
        return self.devices()

    def mute(self, device: Optional[str] = None, mute: Optional[bool] = None):
        if mute is not None:
            set_mute(device, mute)
        return self.devices()
