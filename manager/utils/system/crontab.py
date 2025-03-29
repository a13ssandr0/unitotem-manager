import os
from re import compile

from crontab import CronTab


class UniCron(CronTab):
    _cron_re = compile(r'unitotem:-\)')

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        if cmd:
            item = super().new(
                '/usr/sbin/' + ('poweroff' if cmd == 'pwr' else 'reboot'),  # command
                'unitotem:-)' + os.urandom(16).hex()  # comment
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

    def serialize(self):
        return [{
            'command': job.command,
            'm': int(str(job.minute)),
            'h': int(str(job.hour)),
            'dom': int(str(job.dom)),
            'mon': int(str(job.month)),
            'dow': int(str(job.dow)),
            'enabled': job.enabled,
            'comment': job.comment
        } for job in self.find_comment(self._cron_re)]


CRONTAB = UniCron()  # user='root')
