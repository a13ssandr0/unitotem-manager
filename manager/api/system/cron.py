from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.system.crontab import CRONTAB


class Cron(WSAPIBase):
    @staticmethod
    def getJobs():
        return WSBroadcast(jobs=CRONTAB.read().serialize())

    def addJob(self, command: str = '', minute=None, hour=None, dayOfMonth=None, month=None, dayOfWeek=None):
        with CRONTAB:
            CRONTAB.new(command, minute, hour, dayOfMonth, month, dayOfWeek)
        return self.getJobs()

    def setJobEnabled(self, uuid: str, state: bool):
        try:
            with CRONTAB:
                next(CRONTAB.findById(uuid)).enable(state)
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
        yield self.getJobs()

    def deleteJob(self, uuid: str):
        with CRONTAB:
            CRONTAB.removeById(uuid)
        return self.getJobs()

    def editJob(self, uuid: str, command: str = '', minute=None, hour=None, dayOfMonth=None, month=None,
                  dayOfWeek=None):
        try:
            with CRONTAB:
                _job = next(CRONTAB.findById(uuid))
                if all([x is not None for x in [minute, hour, dayOfMonth, month, dayOfWeek]]):
                    _job.setall(minute, hour, dayOfMonth, month, dayOfWeek)
                if command == 'pwr':
                    _job.set_command('/usr/sbin/poweroff')
                elif command == 'reb':
                    _job.set_command('/usr/sbin/reboot')
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
        yield self.getJobs()
