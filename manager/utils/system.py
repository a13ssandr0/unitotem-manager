__all__ = [
    "CRONTAB",
    "apt_list_upgrades",
    "apt_update",
    "broadcast_sysinfo",
    "get_apt_log",
    "reboot_required",
]



import os
from datetime import datetime
from os.path import exists
from re import compile
from select import select
from subprocess import PIPE, Popen, run
from time import time

from crontab import CronTab
from psutil import (boot_time, sensors_battery, sensors_fans,
                    sensors_temperatures, virtual_memory)

from utils.ws.wsmanager import WSManager
from .cpu import cpu_times_percent

upgradableRe = compile(r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")

_last_log = []
_upd_list_cache = []
REBOOT_REQ       = '/var/run/reboot-required'
REBOOT_REQ_PKGS  = '/var/run/reboot-required.pkgs'

class UniCron(CronTab):

    _cron_re = compile(r'unitotem:-\)')

    def new(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None, **_):
        if cmd:
            item = super().new(
                '/usr/sbin/' + ('poweroff' if cmd == 'pwr' else 'reboot'),  #command
                'unitotem:-)' + os.urandom(16).hex()                        #comment
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



CRONTAB  = UniCron()#user='root')


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
            return list(filter(None, map(lambda x: x.strip(), file.readlines())))
    elif exists(REBOOT_REQ):
        return True
    return False


async def broadcast_sysinfo(ws: WSManager):
    cpu_percent = []
    for x in cpu_times_percent(None):
        y = x._asdict()
        y['total'] = round(sum(x)-x.idle-x.guest-x.guest_nice-x.iowait,1)
        cpu_percent.append(y)
    await ws.broadcast('settings/info',
        uptime = str(datetime.fromtimestamp(time()) - datetime.fromtimestamp(boot_time())).split('.')[0],
        cpu = cpu_percent,
        battery = sensors_battery()._asdict() if sensors_battery() else None,
        fans = [(f'fan-{k}-{x.label or n}'.replace(" ", ""), x.current) for k,v in sensors_fans().items() for n,x in enumerate(v)],
        temperatures = [(f'temp-{k}-{x.label or n}'.replace(" ", ""), x.current) for k,v in sensors_temperatures().items() for n,x in enumerate(v)],
        vmem = virtual_memory()._asdict(),
    )