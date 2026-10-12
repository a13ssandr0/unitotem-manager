from typing import Optional

from pydantic import ValidationError

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models.timers import ACTIONS, SCREEN_ACTIONS, Timer, timer_manager


def _invalid(exc: ValidationError) -> WSResponse:
    return WSResponse(error='Invalid timer', extra='; '.join(e['msg'].removeprefix('Value error, ') for e in exc.errors()))


class Timers(WSAPIBase):
    """Timers. The names (Settings/Timers/...) are kept from when they were crontab entries: the schedule fields are
    still the five of cron, but UniTotem runs them itself."""

    @staticmethod
    def jobs():
        return WSBroadcast(jobs=timer_manager.serialize(), actions=sorted(ACTIONS))

    def add_job(self, command: str, minute: str, hour: str, day_of_month: str, month: str, day_of_week: str,
                output: Optional[str] = None, mechanism: str = 'auto'):
        """Every field of the schedule is required: a missing one would otherwise mean '*'."""
        if command not in ACTIONS and command not in SCREEN_ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted([*ACTIONS, *SCREEN_ACTIONS])}')
            return
        try:
            timer_manager.add(Timer(action=command, minute=minute, hour=hour, day_of_month=day_of_month, month=month,
                                    day_of_week=day_of_week, output=output, mechanism=mechanism))
        except ValidationError as exc:
            yield _invalid(exc)
            return
        yield self.jobs()

    def set_job_enabled(self, uuid: str, state: bool):
        timer = timer_manager.timers.get(uuid)
        if timer is None:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        timer.enabled = state
        timer_manager.save()
        yield self.jobs()

    def delete_job(self, uuid: str):
        timer_manager.remove(uuid)
        return self.jobs()

    def edit_job(self, uuid: str, command: str = '', minute=None, hour=None, day_of_month=None, month=None,
                 day_of_week=None, output: Optional[str] = None, mechanism: Optional[str] = None):
        old = timer_manager.timers.get(uuid)
        if old is None:
            yield WSResponse(error='Not found', extra='Requested job does not exist')
            return
        if command and command not in ACTIONS and command not in SCREEN_ACTIONS:
            yield WSResponse(error='Unknown action',
                             extra=f'{command!r} is not one of {sorted([*ACTIONS, *SCREEN_ACTIONS])}')
            return
        if command and (command in SCREEN_ACTIONS) != (old.action in SCREEN_ACTIONS):
            yield WSResponse(error='Cannot change the kind of a timer', extra='Delete it and create a new one')
            return
        fields = {'minute': minute, 'hour': hour, 'day_of_month': day_of_month, 'month': month,
                  'day_of_week': day_of_week}
        try:
            new = Timer(**{**old.model_dump(),
                           **{k: v for k, v in fields.items() if v is not None},
                           'action': command or old.action,
                           'output': output or old.output,
                           'mechanism': mechanism or old.mechanism})
        except ValidationError as exc:
            yield _invalid(exc)
            return
        timer_manager.timers[uuid] = new
        timer_manager.save()
        yield self.jobs()
