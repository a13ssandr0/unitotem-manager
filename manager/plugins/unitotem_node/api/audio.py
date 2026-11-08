__all__ = ["Audio"]

import math
from typing import Optional

from loguru import logger
from pulsectl import PulseError

from utils.models.user import UserPerms
from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from unitotem_node.system.audio import get_audio_devices, get_default_audio_device, set_default_audio_device, set_volume, \
    set_mute


class Audio(WSAPIBase):
    @UserPerms.requires.audio
    def devices(self):
        try:
            return WSBroadcast(self.devices, devices=get_audio_devices(), default=get_default_audio_device(),
                               available=True)
        except PulseError as e:
            # No sound server (a headless node, a VM without audio): an empty list, not an error on every page load
            logger.warning('Audio not available: {}', e)
            return WSBroadcast(self.devices, devices=[], default=None, available=False)

    @UserPerms.requires.audio
    def set_default_device(self, device: str):
        set_default_audio_device(device)
        return self.devices()

    @UserPerms.requires.audio
    def set_volume(self, volume: float, device: Optional[str] = None):
        """`device` None means the default one. Volume is a 0.0-1.0 level: values outside it are clamped; NaN, infinity and
        non-numbers are refused (ValueError) before anything is sent to Pulse."""
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume):
            raise ValueError(f'{volume!r} is not a volume')
        set_volume(device, min(max(float(volume), 0.0), 1.0))
        return self.devices()

    @UserPerms.requires.audio
    def set_mute(self, mute: bool, device: Optional[str] = None):
        """`device` None means the default one."""
        set_mute(device, mute)
        return self.devices()
