__all__ = [
    "CRONTAB",
    "apt_list_upgrades",
    "apt_update",
    "get_apt_log",
    "reboot_required",
]



from os.path import exists
from re import compile
from select import select
from subprocess import PIPE, Popen, run
from uuid import uuid4

from crontab import CronTab

upgradableRe = compile(r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")

_last_log = []
REBOOT_REQ       = '/var/run/reboot-required'
REBOOT_REQ_PKGS  = '/var/run/reboot-required.pkgs'

class UniCron(CronTab):

    _cron_re = compile(r'unitotem:-\)')

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        if cmd:
            item = super().new(
                '/usr/sbin/' + ('poweroff' if cmd == 'pwr' else 'reboot'),  #command
                'unitotem:-)' + str(uuid4())                                #comment
            )
            if None not in [m, h, dom, mon, dow]:
                item.setall(m,h,dom,mon,dow) #time
            return True
        else:
            return False


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



CRONTAB  = UniCron(user='root')


#https://serverfault.com/questions/300749/apt-get-update-upgrade-list-without-changing-anything


def apt_update(upgrade=False):
    global _last_log
    cmd = ['/usr/bin/apt-get', 'dist-upgrade', '-y'] if upgrade else ['/usr/bin/apt-get', 'update']
    _last_log.clear()
    with Popen(cmd, stdout=PIPE, stderr=PIPE, bufsize=0) as proc:
        yield True
        sout = proc.stdout
        serr = proc.stderr
        while proc.poll() is None:
            descriptors = select([sout, serr], [], [])[0]
            for d in descriptors:
                if d is sout and sout != None:
                    line = (True, sout.readline().decode())
                    _last_log.append(line)
                    yield line
                elif d is serr and serr != None:
                    line = (False, serr.readline().decode())
                    _last_log.append(line)
                    yield line
    yield False

def get_apt_log():
    return _last_log

def apt_list_upgrades():
    return list(map(lambda x: upgradableRe.match(x.strip()).groupdict(), # type: ignore
            filter(lambda l: 'upgradable' in l,
                run(['/usr/bin/apt', 'list', '--upgradable'],
                    stdout=PIPE, stderr=PIPE, env={'LANG':'C'}
                    ).stdout.decode().splitlines())))

def reboot_required():
    if exists(REBOOT_REQ_PKGS):
        with open(REBOOT_REQ_PKGS, 'r') as file:
            return filter(None, map(lambda x: x.strip(), file))
    elif exists(REBOOT_REQ):
        return True
    return False