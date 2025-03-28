from utils.system.crontab import CRONTAB
from ws.responses import WSBroadcast, WSResponse
from ws.wsmanager import WSAPIBase


# class APT(WSAPIBase):
#     async def update(self, do_upgrade: bool = False):
#         global APT_THREAD
#         if not APT_THREAD.is_alive():
#             loop = asyncio.get_running_loop()
#             def apt():
#                 for line in apt_update(do_upgrade):
#                     if line == True:
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/start', upgrading=do_upgrade), loop)
#                     elif line == False:
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/end', upgrading=do_upgrade), loop)
#                     elif isinstance(line, tuple):
#                         asyncio.run_coroutine_threadsafe(
#                               self.ws.broadcast('Settings/Update/progress', upgrading=do_upgrade,
#                                           is_stdout=line[0], data=line[1]), loop)
#
#             APT_THREAD = Thread(target=apt, name=('upgrade' if do_upgrade else 'update'))
#             APT_THREAD.start()
#             return WSBroadcast('Settings/Update/status', status=APT_THREAD.name, log=get_apt_log())
#
#     async def list(self):
#         return WSBroadcast('Settings/Update/list', updates=apt_list_upgrades())
#
#     async def reboot_required(self):
#         return WSBroadcast('Settings/Update/reboot_required', reboot=reboot_required())
#
#     async def status(self):
#         return WSBroadcast('Settings/Update/status',
#                   status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log())

class Cron(WSAPIBase):
    @staticmethod
    def getJobs():
        return WSBroadcast(jobs=CRONTAB.read().serialize())

    def addJob(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
        with CRONTAB:
            CRONTAB.new(cmd, m, h, dom, mon, dow)
        return self.getJobs()

    def setJobEnabled(self, job: str, state: bool):
        try:
            with CRONTAB:
                next(CRONTAB.find_comment(job)).enable(state)
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
        yield self.getJobs()

    def deleteJob(self, job: str):
        with CRONTAB:
            CRONTAB.remove_all(comment=job)
        return self.getJobs()

    def changeJob(self, job: str, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
        try:
            with CRONTAB:
                _job = next(CRONTAB.find_comment(job))
                if all([x is not None for x in [m, h, dom, mon, dow]]):
                    _job.setall(m, h, dom, mon, dow)
                if cmd == 'pwr':
                    _job.set_command('/usr/sbin/poweroff')
                elif cmd == 'reb':
                    _job.set_command('/usr/sbin/reboot')
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
        yield self.getJobs()
