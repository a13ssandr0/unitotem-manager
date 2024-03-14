__all__ = [
    "getAudioDevices",
    "getDefaultAudioDevice",
    "setDefaultAudioDevice",
    "setMute",
    "setVolume",
]

from typing import Optional

# from rpyc import classic as rpyc
from pulsectl import Pulse

from utils.ws.wsmanager import WSAPIBase


def getAudioDevices() -> list[dict[str, str | bool | float]]:
    with Pulse() as pulse:
        default_dev = pulse.server_info().default_sink_name
        return [{'name': sink.name, 'description': sink.description, 'mute': bool(sink.mute),
                 'volume': sink.volume.value_flat, 'default': sink.name == default_dev} for sink in pulse.sink_list()]


def getDefaultAudioDevice() -> str:
    with Pulse() as pulse:
        return pulse.server_info().default_sink_name


def setDefaultAudioDevice(dev: str):
    with Pulse() as pulse:
        pulse.default_set(pulse.get_sink_by_name(dev))


def setVolume(dev: str | None, volume: float):
    with Pulse() as pulse:
        pulse.volume_set_all_chans(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), volume)


def setMute(dev: str | None, mute: bool):
    with Pulse() as pulse:
        pulse.mute(pulse.get_sink_by_name(
            dev or pulse.server_info().default_sink_name), mute)


class Audio(WSAPIBase):
    async def default(self, device: Optional[str] = None):
        if device is not None:
            setDefaultAudioDevice(device)
        await self.devices()

    async def devices(self):
        await self.ws.broadcast('Settings/Audio/devices', devices=getAudioDevices())

    async def volume(self, device: Optional[str] = None, volume: Optional[float] = None):
        if volume is not None:
            setVolume(device, volume)
        await self.devices()

    async def mute(self, device: Optional[str] = None, mute: Optional[bool] = None):
        if mute is not None:
            setMute(device, mute)
        await self.devices()
