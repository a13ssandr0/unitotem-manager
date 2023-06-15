__all__ = [
    "cpu_times",
    "cpu_times_percent",
]



from time import sleep

from psutil import _cpu_times_deltas, _cpu_tot_time  # type: ignore
from psutil._common import LINUX, get_procfs_path, open_binary
from psutil._pslinux import CLOCK_TICKS, scputimes, set_scputimes_ntuple

# reimplementation of psutil CPU related functions to get all CPU data at once
# orginal implementation https://github.com/giampaolo/psutil


# =====================================================================
# --- CPU related functions
# =====================================================================

def cpu_times():
    """Return a list of namedtuple representing the CPU times
    for every CPU available on the system.
    """
    
    """Return system-wide CPU times as a namedtuple.
    Every CPU time represents the seconds the CPU has spent in the
    given mode. The namedtuple's fields availability varies depending on the
    platform:

     - user
     - system
     - idle
     - nice (UNIX)
     - iowait (Linux)
     - irq (Linux, FreeBSD)
     - softirq (Linux)
     - steal (Linux >= 2.6.11)
     - guest (Linux >= 2.6.24)
     - guest_nice (Linux >= 3.2.0)

    When *percpu* is True return a list of namedtuples for each CPU.
    First element of the list refers to first CPU, second element
    to second CPU and so on.
    The order of the list is consistent across calls.
    """
    procfs_path = get_procfs_path()
    set_scputimes_ntuple(procfs_path)
    cpus = []
    with open_binary('%s/stat' % procfs_path) as f:
        for line in f:
            if line.startswith(b'cpu'):
                values = line.split()
                fields = values[1:len(scputimes._fields) + 1]
                fields = [float(x) / CLOCK_TICKS for x in fields]
                entry = scputimes(*fields)
                cpus.append(entry)
        return cpus


try:
    _last_cpu_times = cpu_times()
except Exception:
    # Don't want to crash at import time.
    _last_cpu_times = None


# def cpu_percent(interval=None, percpu=False):
#     """Return a float representing the current system-wide CPU
#     utilization as a percentage.

#     When *interval* is > 0.0 compares system CPU times elapsed before
#     and after the interval (blocking).

#     When *interval* is 0.0 or None compares system CPU times elapsed
#     since last call or module import, returning immediately (non
#     blocking). That means the first time this is called it will
#     return a meaningless 0.0 value which you should ignore.
#     In this case is recommended for accuracy that this function be
#     called with at least 0.1 seconds between calls.

#     When *percpu* is True returns a list of floats representing the
#     utilization as a percentage for each CPU.
#     First element of the list refers to first CPU, second element
#     to second CPU and so on.
#     The order of the list is consistent across calls.

#     Examples:

#       >>> # blocking, system-wide
#       >>> psutil.cpu_percent(interval=1)
#       2.0
#       >>>
#       >>> # blocking, per-cpu
#       >>> psutil.cpu_percent(interval=1, percpu=True)
#       [2.0, 1.0]
#       >>>
#       >>> # non-blocking (percentage since last call)
#       >>> psutil.cpu_percent(interval=None)
#       2.9
#       >>>
#     """
#     global _last_cpu_times
#     global _last_per_cpu_times
#     blocking = interval is not None and interval > 0.0
#     if interval is not None and interval < 0:
#         raise ValueError("interval is not positive (got %r)" % interval)

#     def calculate(t1, t2):
#         times_delta = _cpu_times_deltas(t1, t2)
#         all_delta = _cpu_tot_time(times_delta)
#         busy_delta = _cpu_busy_time(times_delta)

#         try:
#             busy_perc = (busy_delta / all_delta) * 100
#         except ZeroDivisionError:
#             return 0.0
#         else:
#             return round(busy_perc, 1)

#     # system-wide usage
#     if not percpu:
#         if blocking:
#             t1 = cpu_times()
#             sleep(interval)
#         else:
#             t1 = _last_cpu_times
#             if t1 is None:
#                 # Something bad happened at import time. We'll
#                 # get a meaningful result on the next call. See:
#                 # https://github.com/giampaolo/psutil/pull/715
#                 t1 = cpu_times()
#         _last_cpu_times = cpu_times()
#         return calculate(t1, _last_cpu_times)
#     # per-cpu usage
#     else:
#         ret = []
#         if blocking:
#             tot1 = cpu_times(percpu=True)
#             sleep(interval)
#         else:
#             tot1 = _last_per_cpu_times
#             if tot1 is None:
#                 # Something bad happened at import time. We'll
#                 # get a meaningful result on the next call. See:
#                 # https://github.com/giampaolo/psutil/pull/715
#                 tot1 = cpu_times(percpu=True)
#         _last_per_cpu_times = cpu_times(percpu=True)
#         for t1, t2 in zip(tot1, _last_per_cpu_times):
#             ret.append(calculate(t1, t2))
#         return ret


# Use separate global vars for cpu_times_percent() so that it's
# independent from cpu_percent() and they can both be used within
# the same program.
_last_cpu_times_2 = _last_cpu_times
# _last_per_cpu_times_2 = _last_per_cpu_times


def cpu_times_percent(interval=None):
    """Same as cpu_percent() but provides utilization percentages
    for each specific CPU time as is returned by cpu_times().
    For instance, on Linux we'll get:

      >>> cpu_times_percent()
      cpupercent(user=4.8, nice=0.0, system=4.8, idle=90.5, iowait=0.0,
                 irq=0.0, softirq=0.0, steal=0.0, guest=0.0, guest_nice=0.0)
      >>>

    *interval* and *percpu* arguments have the same meaning as in
    cpu_percent().
    """
    global _last_cpu_times_2
    global _last_per_cpu_times_2
    blocking = interval is not None and interval > 0.0
    if interval is not None and interval < 0:
        raise ValueError("interval is not positive (got %r)" % interval)

    def calculate(t1, t2):
        nums = []
        times_delta = _cpu_times_deltas(t1, t2)
        all_delta = _cpu_tot_time(times_delta)
        # "scale" is the value to multiply each delta with to get percentages.
        # We use "max" to avoid division by zero (if all_delta is 0, then all
        # fields are 0 so percentages will be 0 too. all_delta cannot be a
        # fraction because cpu times are integers)
        scale = 100.0 / max(1, all_delta)
        for field_delta in times_delta:
            field_perc = field_delta * scale
            field_perc = round(field_perc, 1)
            # make sure we don't return negative values or values over 100%
            field_perc = min(max(0.0, field_perc), 100.0)
            nums.append(field_perc)
        return scputimes(*nums)


    ret = []
    if blocking:
        tot1 = cpu_times()
        sleep(interval) # type: ignore
    else:
        tot1 = _last_cpu_times_2
        if tot1 is None:
            # Something bad happened at import time. We'll
            # get a meaningful result on the next call. See:
            # https://github.com/giampaolo/psutil/pull/715
            tot1 = cpu_times()
    _last_cpu_times_2 = cpu_times()
    for t1, t2 in zip(tot1, _last_cpu_times_2):
        ret.append(calculate(t1, t2))
    return ret