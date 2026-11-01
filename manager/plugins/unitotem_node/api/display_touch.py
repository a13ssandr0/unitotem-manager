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
from unitotem_node.system.touch_setup import SetupRefused, touch_setup


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
        touch_map.set_calibration(key, None)
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

    def setup(self):
        """The guided setup now running (phase 'idle' when there is none): the step, the target the totem shows, a message. Pushed at every change."""
        return WSBroadcast(self.setup, **touch_setup.state())

    async def start_setup(self, key: str, method: str):
        """Starts the guided setup of a touch screen: `method` 'corners' (touch the four corners, no precision) or 'points' (touch five targets
        exactly). The totem's screen shows the targets; the state is pushed under `setup`. Nothing is stored before `confirm_setup`."""
        try:
            await touch_setup.start(key, method)
        except SetupRefused as exc:
            return WSResponse(self.setup, error=str(exc))

    async def confirm_setup(self):
        """The visitor tried the result and it is right: keep it."""
        try:
            await touch_setup.confirm()
        except SetupRefused as exc:
            return WSResponse(self.setup, error=str(exc))
        return WSBroadcast(self.devices, **touch_service.snapshot())

    async def cancel_setup(self):
        """Ends the setup without keeping anything; the stored configuration is back at once."""
        await touch_setup.cancel()

    async def reset(self, key: str):
        """Back to automatic: the panel follows its screen with no correction (what a freshly plugged panel is)."""
        await touch_setup.reset(key)
        return WSBroadcast(self.devices, **touch_service.snapshot())
