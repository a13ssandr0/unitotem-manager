"""
Touch screens and the output each one belongs to.

Paths: Settings/Display/Touch/<function> (bound into unitotem_node.api.display.Display). The device list is also pushed
by the touch service whenever a device or a screen appears, disappears or rotates, under the same `devices` target.
"""
from typing import Optional

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from unitotem_node.models.touch_map import touch_map
from unitotem_node.system.touch_service import touch_service


class Touch(WSAPIBase):
    def devices(self):
        """Touch screens found on this machine with their output (None: automatic), the remembered ones that are not
        plugged in, and the outputs to choose from."""
        return WSBroadcast(self.devices, **touch_service.snapshot())

    async def set_output(self, key: str, output: Optional[str] = None):
        """Map a touch screen, by its stable identity, to an output; None goes back to automatic (the whole screen)."""
        try:
            touch_map.set(key, output)
        except ValueError as exc:
            return WSResponse(error=str(exc))
        touch_map.save()
        await touch_service.refresh()
        return WSBroadcast(self.devices, **touch_service.snapshot())

    async def forget(self, key: str):
        """Drop the mapping of a device that is not plugged in any more."""
        touch_map.set(key, None)
        touch_map.set_orientation(key, 0, 0)
        touch_map.save()
        await touch_service.refresh()
        return WSBroadcast(self.devices, **touch_service.snapshot())

    async def set_orientation(self, key: str, turn: int = 0, flip: int = 0):
        """Turn and/or mirror the touch input of a panel that is not glued to its screen and has another natural orientation (a landscape
        screen with a portrait panel). Same convention as a window's orientation: `turn` quarter turns clockwise, `flip` 0 none, 1 left-right,
        2 top-bottom. A touch screen built into its monitor needs nothing: it follows the screen's own turn by itself."""
        try:
            touch_map.set_orientation(key, turn, flip)
        except ValueError as exc:
            return WSResponse(error=str(exc))
        touch_map.save()
        await touch_service.refresh()
        return WSBroadcast(self.devices, **touch_service.snapshot())
