from crontab import CronSlices

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.system.crontab import ACTIONS, CRONTAB


def _schedule_error(minute, hour, dayOfMonth, month, dayOfWeek):
    """None if the five fields are a valid cron schedule, an error string if not.

    Checked BEFORE the crontab is opened, not inside a try around the mutation:
    python-crontab's CronTab.__exit__ calls write() unconditionally, exception
    or not, so a slice rejected halfway through setall() would be written back
    half-applied. Validating first keeps a bad request from touching the file
    at all.

    The fields are user text - the web UI offers plain numbers and '*' but any
    cron slice is legal ('*/15', '1-5', '0,30') and the backend preserves them -
    so this is the only thing standing between a typo and a 500.
    """
    fields = [minute, hour, dayOfMonth, month, dayOfWeek]
    if any(f is None for f in fields):
        return None     # "leave the schedule alone", handled by the caller
    if not CronSlices.is_valid(*(str(f) for f in fields)):
        return ' '.join(str(f) for f in fields)
    return None


class Cron(WSAPIBase):
    @staticmethod
    def getJobs():
        return WSBroadcast(jobs=CRONTAB.read().serialize(), actions=sorted(ACTIONS))

    def addJob(self, command: str = '', minute=None, hour=None, dayOfMonth=None, month=None, dayOfWeek=None):
        if command not in ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted(ACTIONS)}')
            return
        bad = _schedule_error(minute, hour, dayOfMonth, month, dayOfWeek)
        if bad:
            yield WSResponse(error='Invalid schedule', extra=f'{bad!r} is not a valid cron schedule')
            return
        with CRONTAB:
            CRONTAB.new(command, minute, hour, dayOfMonth, month, dayOfWeek)
        yield self.getJobs()

    def setJobEnabled(self, uuid: str, state: bool):
        try:
            with CRONTAB:
                next(CRONTAB.findById(uuid)).enable(state)
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        yield self.getJobs()

    def deleteJob(self, uuid: str):
        with CRONTAB:
            CRONTAB.removeById(uuid)
        return self.getJobs()

    def editJob(self, uuid: str, command: str = '', minute=None, hour=None, dayOfMonth=None, month=None,
                  dayOfWeek=None):
        # An empty command means "schedule only, leave the action alone", which
        # is what a job this version did not write can be given: its schedule
        # stays editable, its command must not be rewritten into one of ours.
        if command and command not in ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted(ACTIONS)}')
            return
        bad = _schedule_error(minute, hour, dayOfMonth, month, dayOfWeek)
        if bad:
            yield WSResponse(error='Invalid schedule', extra=f'{bad!r} is not a valid cron schedule')
            return
        try:
            with CRONTAB:
                _job = next(CRONTAB.findById(uuid))
                if all([x is not None for x in [minute, hour, dayOfMonth, month, dayOfWeek]]):
                    _job.setall(minute, hour, dayOfMonth, month, dayOfWeek)
                if command:
                    # Looked up, never interpolated - see utils.system.crontab.ACTIONS
                    _job.set_command(ACTIONS[command])
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        yield self.getJobs()
