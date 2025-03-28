__all__ = ["Audio"]

from typing import Optional

from api.models import UserPerms
from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.system.audio import get_audio_devices, set_default_audio_device, set_volume, set_mute


class Audio(WSAPIBase):
    @UserPerms.requires.audio
    def devices(self):
        return WSBroadcast(self.devices, devices=get_audio_devices())

    @UserPerms.requires.audio
    def default(self, device: Optional[str] = None):
        if device is not None:
            set_default_audio_device(device)
        return self.devices()

    @UserPerms.requires.audio
    def volume(self, device: Optional[str] = None, volume: Optional[float] = None):
        if volume is not None:
            set_volume(device, volume)
        return self.devices()

    @UserPerms.requires.audio
    def mute(self, device: Optional[str] = None, mute: Optional[bool] = None):
        if mute is not None:
            set_mute(device, mute)
        return self.devices()
