__all__ = ["Audio"]

from typing import Optional

from utils.models.user import UserPerms
from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.system.audio import get_audio_devices, get_default_audio_device, set_default_audio_device, set_volume, \
    set_mute


class Audio(WSAPIBase):
    @UserPerms.requires.audio
    def devices(self):
        return WSBroadcast(self.devices, devices=get_audio_devices(), default=get_default_audio_device())

    @UserPerms.requires.audio
    def set_default_device(self, device: str):
        set_default_audio_device(device)
        return self.devices()

    @UserPerms.requires.audio
    def set_volume(self, volume: float, device: Optional[str] = None):
        """`device` None means the default one."""
        set_volume(device, volume)
        return self.devices()

    @UserPerms.requires.audio
    def set_mute(self, mute: bool, device: Optional[str] = None):
        """`device` None means the default one."""
        set_mute(device, mute)
        return self.devices()
