from datetime import datetime
from operator import itemgetter
from time import time

from natsort import natsorted
from psutil import boot_time, sensors_battery, sensors_fans, virtual_memory

from utils.system.cpu import cpu_times_percent
from utils.system.lsblk import lsblk
from utils.system.sensors import sensors_temperatures


def get_uptime():
    return str(datetime.fromtimestamp(time()) - datetime.fromtimestamp(boot_time())).split('.')[0]


def get_cpu():
    cpu_percent = []
    for x in cpu_times_percent(None):
        y = x._asdict()
        y['total'] = round(sum(x) - x.idle - x.guest - x.guest_nice - x.iowait, 1)
        cpu_percent.append(y)
    return cpu_percent


def get_battery():
    return sensors_battery()._asdict() if sensors_battery() else None


def get_fans():
    return {
        k:
            natsorted([
                {
                    "name" : f'{x.label or n}'.replace(" ", ""),
                    "speed": x.current
                } for n, x in enumerate(v)
            ], key=itemgetter("name"))
        for k, v in natsorted(sensors_fans().items(), key=itemgetter(0))
    }


def get_temps():
    return {
        k:
            natsorted([
                {
                    "name"    : f'{x.label or n}'.replace(" ", ""),
                    "current" : x.current,
                    "high"    : x.high,
                    "critical": x.critical
                } for n, x in enumerate(v)
            ], key=itemgetter("name"))
        for k, v in natsorted(sensors_temperatures().items(), key=itemgetter(0))
    }


def get_disks():
    return [blk.model_dump() for blk in lsblk()]


def get_sysinfo():
    return dict(
            uptime=get_uptime(),
            cpu=get_cpu(),
            vmem=virtual_memory()._asdict(),
            disks=get_disks(),
            battery=get_battery(),
            fans=get_fans(),
            temperatures=get_temps(),
    )
