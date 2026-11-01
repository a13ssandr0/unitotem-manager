"""
Monitor control of this node's own outputs: power (X, DDC/CI, CEC, Wake-on-LAN, serial), input source, volume.

Paths: Settings/Display/Screens/<function> (bound into unitotem_node.api.display.Display). Every change answers on `screens`, so
any page showing the outputs is updated; errors the operator can act on come back as an `error` message.
"""
import asyncio
from typing import Optional

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models.user import UserPerms
from unitotem_node.system.screens import control
from unitotem_node.system.screens.control import ScreenControlError


class Screens(WSAPIBase):
    async def _broadcast(self):
        await self.ws.broadcast('Settings/Display/Screens/screens', screens=await control.screens(),
                                probing=control.probing())

    @UserPerms.requires.power
    @UserPerms.requires.audio
    async def screens(self):
        """Every output with the mechanisms available on it, its configuration and what the last probe found."""
        return WSBroadcast(self.screens, screens=await control.screens(), probing=control.probing())

    async def probe(self, name: Optional[str] = None):
        """Find out what each monitor supports (DDC/CI buses and advertised inputs). Slow, runs in the background."""
        await control.probe(name, on_done=self._broadcast)
        return WSBroadcast(self.screens, screens=await control.screens(), probing=True)

    @UserPerms.requires.power
    async def set_power(self, name: str, state: str, mechanism: str = 'auto'):
        """Switch a monitor 'on' or 'off' with one mechanism, or 'auto' to try them in order."""
        try:
            await control.set_power(name, state, mechanism)
        except (ScreenControlError, ValueError) as exc:
            return WSResponse(error=str(exc))
        return WSBroadcast(self.screens, screens=await control.screens(), probing=control.probing())

    @UserPerms.requires.power
    async def power_state(self, name: str):
        """What X, DDC/CI and CEC each say about an output (None where they cannot tell)."""
        return WSBroadcast(self.power_state, name=name, state=await control.power_state(name))

    async def set_input(self, name: str, source: int):
        """Select the input of a monitor over DDC/CI, among the values it advertises."""
        try:
            await control.set_input(name, source)
        except ScreenControlError as exc:
            return WSResponse(error=str(exc))
        return None

    @UserPerms.requires.audio
    async def set_volume(self, name: str, volume: int):
        """The speaker volume of a monitor over DDC/CI, 0 to 100."""
        try:
            await control.set_volume(name, volume)
        except ScreenControlError as exc:
            return WSResponse(error=str(exc))
        return None

    @UserPerms.requires.audio
    async def set_mute(self, name: str, mute: bool):
        try:
            await control.set_mute(name, mute)
        except ScreenControlError as exc:
            return WSResponse(error=str(exc))
        return None

    async def gpio_lines(self):
        """The GPIO chips of this machine and their lines (name, and who uses each), for choosing the line of a screen."""
        from ..system.screens import gpio
        return WSBroadcast(self.gpio_lines, chips=await asyncio.to_thread(gpio.chips))

    async def configure(self, name: str, on_mechanism: Optional[str] = None, off_mechanism: Optional[str] = None,
                        power_on_at_start: Optional[bool] = None, wol: Optional[dict] = None,
                        serial: Optional[dict] = None, gpio: Optional[dict] = None, cec_device: Optional[str] = None,
                        clear_wol: bool = False, clear_serial: bool = False, clear_gpio: bool = False):
        """Change how an output is controlled. `wol` is {mac, broadcast?, port?}, `serial` is
        {port, baud?, on_command, off_command}, `gpio` is {chip, line, mode?, active_low?, pulse_ms?};
        clear_wol / clear_serial / clear_gpio remove them."""
        changes = {k: v for k, v in dict(on_mechanism=on_mechanism, off_mechanism=off_mechanism,
                                         power_on_at_start=power_on_at_start, wol=wol, serial=serial, gpio=gpio,
                                         cec_device=cec_device).items() if v is not None}
        if clear_wol:
            changes['wol'] = None
        if clear_serial:
            changes['serial'] = None
        if clear_gpio:
            changes['gpio'] = None
        try:
            control.configure(name, **changes)
        except (ScreenControlError, ValueError) as exc:
            return WSResponse(error=str(exc))
        return WSBroadcast(self.screens, screens=await control.screens(), probing=control.probing())
