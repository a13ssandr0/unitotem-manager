import asyncio
from asyncio import run_coroutine_threadsafe as run_coro

from loguru import logger

from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from utils.system.apt import apt


class Update(WSAPIBase):
    def __init__(self, ws, remote_ws):
        super().__init__(ws, remote_ws)

        loop = asyncio.get_event_loop()
        logger.debug('Got event loop {}', id(loop))

        apt.on_start(lambda upgrading: run_coro(
                self.ws.broadcast('Settings/Update/start',
                                  upgrading=upgrading),
                loop))

        apt.on_progress(lambda upgrading, is_stdout, data: run_coro(
                self.ws.broadcast('Settings/Update/progress',
                                  upgrading=upgrading, is_stdout=is_stdout, data=data),
                loop))

        apt.on_end(lambda upgrading, returncode: run_coro(
                self.ws.broadcast('Settings/Update/end',
                                  upgrading=upgrading, returncode=returncode),
                loop))

        apt.on_end_post(lambda upgrading: self.__on_end_post(loop))

    def __on_end_post(self, loop):
        for resp in [self.list(), self.reboot_required()]:
            run_coro(self.ws.broadcast(resp.target, **resp.kwargs), loop)



    # async def update(self, do_upgrade: bool = False):
    #     if not APT_THREAD.is_alive():
    #         def _apt():
    #             for line in apt.update(do_upgrade):
    #                 if line is True:
    #                     asyncio.run_coroutine_threadsafe(
    #                           self.ws.broadcast('Settings/Update/start', upgrading=do_upgrade), loop)
    #                 elif line is False:
    #                     asyncio.run_coroutine_threadsafe(
    #                           self.ws.broadcast('Settings/Update/end', upgrading=do_upgrade), loop)
    #                 elif isinstance(line, tuple):
    #                     asyncio.run_coroutine_threadsafe(
    #                           self.ws.broadcast('Settings/Update/progress', upgrading=do_upgrade,
    #                                       is_stdout=line[0], data=line[1]), loop)
    #
    #         apt.update.run_threaded()
    #
    #         APT_THREAD = Thread(target=_apt, name=('upgrade' if do_upgrade else 'update'))
    #         APT_THREAD.start()
    #         return self.status()

    @staticmethod
    def list():
        return WSBroadcast(updates=apt.list_upgradable())

    @staticmethod
    def reboot_required():
        return WSBroadcast(reboot=apt.reboot_required)

    @staticmethod
    def status():
        return WSBroadcast(status=apt.status, log=apt.log, returncode=apt.returncode)

    def update(self):
        apt.update()
        return self.status()

    def upgrade(self):
        apt.upgrade()
        return self.status()
