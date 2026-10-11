from utils.models.user import UserPerms
from api.ws.wsmanager import WSAPIBase


class Power(WSAPIBase):
    @staticmethod
    @UserPerms.requires.power
    async def reboot():
        from utils.system.dbus_system import system_reboot
        await system_reboot()

    @staticmethod
    @UserPerms.requires.power
    async def poweroff():
        from utils.system.dbus_system import system_poweroff
        await system_poweroff()
