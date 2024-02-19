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
from starlette.websockets import WebSocket

from utils.ws.wsmanager import WSManager
from .cpu import cpu_times_percent
from .ws.wsmanager import WSAPIBase

upgradableRe = compile(
    r"(?P<package>.*)/(?P<origin>.*?) (?P<new_version>.*?) (?P<architecture>.*?) \[upgradable from: (?P<old_version>.*?)]")

_last_log = []
_upd_list_cache = []
REBOOT_REQ = '/var/run/reboot-required'
REBOOT_REQ_PKGS = '/var/run/reboot-required.pkgs'


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


# https://serverfault.com/questions/300749/apt-get-update-upgrade-list-without-changing-anything


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
                if d is sout and sout is not None:
                    line = (True, sout.readline().decode())
                    _last_log.append(line)
                    yield line
                elif d is serr and serr is not None:
                    line = (False, serr.readline().decode())
                    _last_log.append(line)
                    yield line
    yield False


def get_apt_log():
    return _last_log


def apt_list_upgrades():
    return list(map(lambda x: upgradableRe.match(x.strip()).groupdict(),  # type: ignore
                    filter(lambda l: 'upgradable' in l,
                           run(['/usr/bin/apt', 'list', '--upgradable'],
                               stdout=PIPE, stderr=PIPE, env={'LANG': 'C'}
                               ).stdout.decode().splitlines())))


def reboot_required():
    if exists(REBOOT_REQ_PKGS):
        with open(REBOOT_REQ_PKGS, 'r') as file:
            return list(filter(None, map(lambda x: x.strip(), file.readlines())))
    elif exists(REBOOT_REQ):
        return True
    return False


# noinspection PyProtectedMember
async def broadcast_sysinfo(ws: WSManager):
    cpu_percent = []
    for x in cpu_times_percent(None):
        y = x._asdict()
        y['total'] = round(sum(x) - x.idle - x.guest - x.guest_nice - x.iowait, 1)
        cpu_percent.append(y)
    await ws.broadcast('settings/info',
                       uptime=str(datetime.fromtimestamp(time()) - datetime.fromtimestamp(boot_time())).split('.')[0],
                       cpu=cpu_percent,
                       battery=sensors_battery()._asdict() if sensors_battery() else None,
                       fans=[(f'fan-{k}-{x.label or n}'.replace(" ", ""), x.current) for k, v in sensors_fans().items()
                             for n, x in enumerate(v)],
                       temperatures=[(f'temp-{k}-{x.label or n}'.replace(" ", ""), x.current) for k, v in
                                     sensors_temperatures().items() for n, x in enumerate(v)],
                       vmem=virtual_memory()._asdict(),
                       )


class Settings(WSAPIBase):
    # class APT(WSAPIBase):
    #     async def update(self, do_upgrade: bool = False):
    #         global APT_THREAD
    #         if not APT_THREAD.is_alive():
    #             loop = asyncio.get_running_loop()
    #             def apt():
    #                 for line in apt_update(do_upgrade):
    #                     if line == True:
    #                         asyncio.run_coroutine_threadsafe(
    #                               self.ws.broadcast('settings/update/start', upgrading=do_upgrade), loop)
    #                     elif line == False:
    #                         asyncio.run_coroutine_threadsafe(
    #                               self.ws.broadcast('settings/update/end', upgrading=do_upgrade), loop)
    #                     elif isinstance(line, tuple):
    #                         asyncio.run_coroutine_threadsafe(
    #                               self.ws.broadcast('settings/update/progress', upgrading=do_upgrade,
    #                                           is_stdout=line[0], data=line[1]), loop)
    #
    #             APT_THREAD = Thread(target=apt, name=('upgrade' if do_upgrade else 'update'))
    #             APT_THREAD.start()
    #             await self.ws.broadcast('settings/update/status', status=APT_THREAD.name, log=get_apt_log())
    #
    #     async def list(self):
    #         await self.ws.broadcast('settings/update/list', updates=apt_list_upgrades())
    #
    #     async def reboot_required(self):
    #         await self.ws.broadcast('settings/update/reboot_required', reboot=reboot_required())
    #
    #     async def status(self):
    #         await self.ws.broadcast('settings/update/status',
    #                   status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log())

    class Cron(WSAPIBase):
        async def getJobs(self):
            CRONTAB.read()
            await self.ws.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

        async def addJob(self, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
            CRONTAB.read()
            if CRONTAB.new(cmd, m, h, dom, mon, dow):
                CRONTAB.write()
            await self.ws.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

        async def setJobEnabled(self, ws: WebSocket, job: str, state: bool):
            CRONTAB.read()
            try:
                next(CRONTAB.find_comment(job)).enable(state)
                CRONTAB.write()
            except StopIteration:
                await self.ws.send(ws, 'error', error='Not found', extra='Requested job does not exist')
            await self.ws.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

        async def deleteJob(self, job: str):
            CRONTAB.read()
            CRONTAB.remove_all(comment=job)
            CRONTAB.write()
            await self.ws.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

        async def changeJob(self, ws: WebSocket, job: str, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
            CRONTAB.read()
            try:
                _job = next(CRONTAB.find_comment(job))
                if all([x is not None for x in [m, h, dom, mon, dow]]):
                    _job.setall(m, h, dom, mon, dow)
                if cmd == 'pwr':
                    _job.set_command('/usr/sbin/poweroff')
                elif cmd == 'reb':
                    _job.set_command('/usr/sbin/reboot')
                CRONTAB.write()
            except StopIteration:
                await self.ws.send(ws, 'error', error='Not found', extra='Requested job does not exist')
            await self.ws.broadcast('settings/cron/job', jobs=CRONTAB.serialize())
