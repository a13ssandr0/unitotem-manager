from crontab import CronSlices

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.system.crontab import ACTIONS, CRONTAB


def _schedule_error(minute, hour, day_of_month, month, day_of_week):
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
    fields = [minute, hour, day_of_month, month, day_of_week]
    if any(f is None for f in fields):
        return None     # "leave the schedule alone", handled by the caller
    if not CronSlices.is_valid(*(str(f) for f in fields)):
        return ' '.join(str(f) for f in fields)
    return None


class Cron(WSAPIBase):
    @staticmethod
    def jobs():
        return WSBroadcast(jobs=CRONTAB.read().serialize(), actions=sorted(ACTIONS))

    def add_job(self, command: str, minute: str, hour: str, day_of_month: str, month: str, day_of_week: str):
        """Every field of the schedule is required: python-crontab's default for a missing one is '*', which
        for a reboot timer means every minute."""
        if command not in ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted(ACTIONS)}')
            return
        bad = _schedule_error(minute, hour, day_of_month, month, day_of_week)
        if bad:
            yield WSResponse(error='Invalid schedule', extra=f'{bad!r} is not a valid cron schedule')
            return
        with CRONTAB:
            CRONTAB.new(command, minute, hour, day_of_month, month, day_of_week)
        yield self.jobs()

    def set_job_enabled(self, uuid: str, state: bool):
        try:
            with CRONTAB:
                next(CRONTAB.findById(uuid)).enable(state)
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        yield self.jobs()

    def delete_job(self, uuid: str):
        with CRONTAB:
            CRONTAB.removeById(uuid)
        return self.jobs()

    def edit_job(self, uuid: str, command: str = '', minute=None, hour=None, day_of_month=None, month=None,
                  day_of_week=None):
        # An empty command means "schedule only, leave the action alone", which
        # is what a job this version did not write can be given: its schedule
        # stays editable, its command must not be rewritten into one of ours.
        if command and command not in ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted(ACTIONS)}')
            return
        bad = _schedule_error(minute, hour, day_of_month, month, day_of_week)
        if bad:
            yield WSResponse(error='Invalid schedule', extra=f'{bad!r} is not a valid cron schedule')
            return
        try:
            with CRONTAB:
                _job = next(CRONTAB.findById(uuid))
                if all([x is not None for x in [minute, hour, day_of_month, month, day_of_week]]):
                    _job.setall(minute, hour, day_of_month, month, day_of_week)
                if command:
                    # Looked up, never interpolated - see utils.system.crontab.ACTIONS
                    _job.set_command(ACTIONS[command])
        except StopIteration:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        yield self.jobs()
