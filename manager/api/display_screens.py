"""
Monitor control of this node's own outputs: power (X, DDC/CI, CEC, Wake-on-LAN, serial), input source, volume.

Paths: Settings/Display/Screens/<function> (bound into api.display.Display). Every change answers on `screens`, so
any page showing the outputs is updated; errors the operator can act on come back as an `error` message.
"""
from typing import Optional

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models.user import UserPerms
from utils.system.screens import control
from utils.system.screens.control import ScreenControlError


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

    async def configure(self, name: str, on_mechanism: Optional[str] = None, off_mechanism: Optional[str] = None,
                        power_on_at_start: Optional[bool] = None, wol: Optional[dict] = None,
                        serial: Optional[dict] = None, cec_device: Optional[str] = None,
                        clear_wol: bool = False, clear_serial: bool = False):
        """Change how an output is controlled. `wol` is {mac, broadcast?, port?}, `serial` is
        {port, baud?, on_command, off_command}; clear_wol / clear_serial remove them."""
        changes = {k: v for k, v in dict(on_mechanism=on_mechanism, off_mechanism=off_mechanism,
                                         power_on_at_start=power_on_at_start, wol=wol, serial=serial,
                                         cec_device=cec_device).items() if v is not None}
        if clear_wol:
            changes['wol'] = None
        if clear_serial:
            changes['serial'] = None
        try:
            control.configure(name, **changes)
        except (ScreenControlError, ValueError) as exc:
            return WSResponse(error=str(exc))
        return WSBroadcast(self.screens, screens=await control.screens(), probing=control.probing())
