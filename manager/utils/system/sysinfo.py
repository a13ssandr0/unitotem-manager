from datetime import datetime
from time import time

from psutil import boot_time, sensors_battery, sensors_fans, sensors_temperatures, virtual_memory

from utils.system.cpu import cpu_times_percent


# noinspection PyProtectedMember
def get_sysinfo():
    cpu_percent = []
    for x in cpu_times_percent(None):
        y = x._asdict()
        y['total'] = round(sum(x) - x.idle - x.guest - x.guest_nice - x.iowait, 1)
        cpu_percent.append(y)
    return dict(
        uptime=str(datetime.fromtimestamp(time()) - datetime.fromtimestamp(boot_time())).split('.')[0],
        cpu=cpu_percent,
        battery=sensors_battery()._asdict() if sensors_battery() else None,
        fans=[(f'fan-{k}-{x.label or n}'.replace(" ", ""), x.current) for k, v in sensors_fans().items()
              for n, x in enumerate(v)],
        temperatures=[(f'temp-{k}-{x.label or n}'.replace(" ", ""), x.current) for k, v in
                      sensors_temperatures().items() for n, x in enumerate(v)],
        vmem=virtual_memory()._asdict(),
    )
