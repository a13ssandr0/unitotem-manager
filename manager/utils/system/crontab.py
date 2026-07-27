import os
from getpass import getuser

from crontab import CronTab


class UniCron(CronTab):
    __job_prefix = 'unitotem:-)'

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        if cmd:
            item = super().new(
                '/usr/sbin/' + ('poweroff' if cmd == 'pwr' else 'reboot'),  # command
                self.__job_prefix + os.urandom(16).hex()  # comment
            )
            if None not in [m, h, dom, mon, dow]:
                item.setall(m, h, dom, mon, dow)  # time
            return True
        else:
            return False

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
        return [{
            'uuid': job.comment.removeprefix(self.__job_prefix),
            'command': job.command,
            'minute': int(str(job.minute)),
            'hour': int(str(job.hour)),
            'dayOfMonth': int(str(job.dom)),
            'month': int(str(job.month)),
            'dayOfWeek': int(str(job.dow)),
            'enabled': job.enabled,
        } for job in self.findById()]


CRONTAB = UniCron(user=getuser())
