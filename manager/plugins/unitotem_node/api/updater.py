from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.models.user import UserPerms
from unitotem_node.system import updater


async def _pushed(ws, result):
    """The result of a command, after the status is pushed once more. (A module function: the endpoint loader exposes every method.)"""
    await ws.broadcast('Settings/Update/status', status=await updater.status())
    return WSBroadcast(result=result)


class Update(WSAPIBase):
    # The status of a running operation is pushed by system/update_progress.py (inotify on the updater's progress file); each command
    # below pushes it once more, so the page also sees what the command did at once.

    async def status(self):
        status = await updater.status()
        return WSBroadcast(status=status)

    async def check(self):
        result = await updater.check()
        if updater.record(result):
            await self.ws.broadcast('Settings/Update/availability', availability=updater.availability)
        return WSBroadcast(check=result)

    @staticmethod
    @UserPerms.requires.none
    def availability():
        """Whether an update was found by the last check: shown as a hint in the navbar of every page."""
        return WSBroadcast(availability=updater.availability)

    async def fetch(self, force: bool = False):
        return await _pushed(self.ws, await updater.fetch(force))

    async def cancel(self):
        return await _pushed(self.ws, await updater.cancel())

    async def apply(self, reboot: bool = True):
        return await _pushed(self.ws, await updater.apply(reboot))

    async def confirm(self):
        return await _pushed(self.ws, await updater.confirm())

    async def rollback(self, reboot: bool = True):
        return await _pushed(self.ws, await updater.rollback(reboot))

    async def copy_to_other(self):
        return await _pushed(self.ws, await updater.copy_to_other())

    async def set_policy(self, mode: str, time: str = '04:00'):
        if mode not in updater.POLICY_MODES:
            return WSBroadcast(result={'error': f'Unknown mode {mode!r}'})
        try:
            result = await updater.set_policy(mode, time)
        except ValueError as exc:
            return WSBroadcast(result={'error': str(exc)})
        await self.ws.broadcast('Settings/Update/status', status=await updater.status())
        return WSBroadcast(result=result)
