from api.ws.responses import WSBroadcast, WSResponse
from utils.models.user import UserPerms
from api.ws.wsmanager import WSAPIBase


class Power(WSAPIBase):
    @staticmethod
    @UserPerms.requires.power
    async def reboot():
        from unitotem_node.system import updater
        if (await updater.status()).get('staged'):
            await updater.reboot()          # an armed update starts only with the updater's own restart
            return
        from unitotem_node.system.dbus_system import system_reboot
        await system_reboot()

    @staticmethod
    @UserPerms.requires.power
    async def poweroff():
        from unitotem_node.system.dbus_system import system_poweroff
        await system_poweroff()

    @staticmethod
    @UserPerms.requires.power
    async def suspend_support():
        """Whether this node can suspend and wake itself up, and if not why (shown by the web UI before offering it)."""
        from unitotem_node.system import suspend
        return WSBroadcast(**await suspend.support())

    @staticmethod
    @UserPerms.requires.power
    async def suspend(wake_at: str):
        """Suspend now and wake up at `wake_at`, a date and time (YYYY-MM-DDTHH:MM) in the node's own local time."""
        from unitotem_node.system import suspend, wake
        from unitotem_node.system.rtc import SuspendRefused
        try:
            await suspend.suspend_until(wake.parse_local(wake_at))
        except (SuspendRefused, wake.WakeError) as exc:
            return WSResponse(error='Cannot suspend', extra=str(exc))
