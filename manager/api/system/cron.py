from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.system.crontab import CRONTAB


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
