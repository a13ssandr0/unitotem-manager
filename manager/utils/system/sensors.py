"""
This module replaces function `sensors_temperatures` of psutil.

In PR #2304 (https://github.com/giampaolo/psutil/pull/2304), not yet merged,
I changed the function to behave more like the linux command sensors:
devices have part of their address in the name to allow accessing
sensors when multiple devices of the same type are present.

At the moment the upstream psutil.sensors_temperatures combine
sensors of said devices into one device making impossible to
distinguish which sensor belongs to which device.
"""
import collections
import glob
import os
import re

from psutil._common import bcat, cat, debug, shwtemp


def __sensors_temperatures():
    """Return hardware (CPU and others) temperatures as a dict
    including hardware name, label, current, max and critical
    temperatures.

    Implementation notes:
    - /sys/class/hwmon looks like the most recent interface to
      retrieve this info, and this implementation relies on it
      only (old distros will probably use something else)
    - lm-sensors on Ubuntu 16.04 relies on /sys/class/hwmon
    - /sys/class/thermal/thermal_zone* is another one but it's more
      difficult to parse
    """
    ret = collections.defaultdict(list)
    basenames = glob.glob('/sys/class/hwmon/hwmon*/temp*_*')
    # CentOS has an intermediate /device directory:
    # https://github.com/giampaolo/psutil/issues/971
    # https://github.com/nicolargo/glances/issues/1060
    basenames.extend(glob.glob('/sys/class/hwmon/hwmon*/device/temp*_*'))
    basenames = sorted(set([x.split('_')[0] for x in basenames]))

    # Only add the coretemp hwmon entries if they're not already in
    # /sys/class/hwmon/
    # https://github.com/giampaolo/psutil/issues/1708
    # https://github.com/giampaolo/psutil/pull/1648
    basenames2 = glob.glob(
        '/sys/devices/platform/coretemp.*/hwmon/hwmon*/temp*_*'
    )
    repl = re.compile('/sys/devices/platform/coretemp.*/hwmon/')
    for name in basenames2:
        altname = repl.sub('/sys/class/hwmon/', name)
        if altname not in basenames:
            basenames.append(name)

    for base in basenames:
        try:
            path = base + '_input'
            current = float(bcat(path)) / 1000.0
            path = os.path.join(os.path.dirname(base), 'name')
            unit_name = cat(path).strip()
            address = os.path.join(os.path.dirname(base), 'device', 'address')
            # Some systems may have multiple devices with same unit_name,
            # for example multiple nvme SSDs, both will have their sensors
            # inside ret['nvme'] making it difficult to know which
            # SSD they refer to
            try:
                unit_name += '-' + ''.join(cat(address).strip().split(':')[1:3]).split('.')[0]
            except:
                # device address file may not always exist, be readable or
                # correctly parsable
                pass
        except (IOError, OSError, ValueError):
            # A lot of things can go wrong here, so let's just skip the
            # whole entry. Sure thing is Linux's /sys/class/hwmon really
            # is a stinky broken mess.
            # https://github.com/giampaolo/psutil/issues/1009
            # https://github.com/giampaolo/psutil/issues/1101
            # https://github.com/giampaolo/psutil/issues/1129
            # https://github.com/giampaolo/psutil/issues/1245
            # https://github.com/giampaolo/psutil/issues/1323
            continue

        high = bcat(base + '_max', fallback=None)
        critical = bcat(base + '_crit', fallback=None)
        label = cat(base + '_label', fallback='').strip()

        if high is not None:
            try:
                high = float(high) / 1000.0
            except ValueError:
                high = None
        if critical is not None:
            try:
                critical = float(critical) / 1000.0
            except ValueError:
                critical = None

        ret[unit_name].append((label, current, high, critical))

    # Indication that no sensors were detected in /sys/class/hwmon/
    if not basenames:
        basenames = glob.glob('/sys/class/thermal/thermal_zone*')
        basenames = sorted(set(basenames))

        for base in basenames:
            try:
                path = os.path.join(base, 'temp')
                current = float(bcat(path)) / 1000.0
                path = os.path.join(base, 'type')
                unit_name = cat(path).strip()
            except (IOError, OSError, ValueError) as err:
                debug(err)
                continue

            trip_paths = glob.glob(base + '/trip_point*')
            trip_points = set([
                '_'.join(os.path.basename(p).split('_')[0:3])
                for p in trip_paths
            ])
            critical = None
            high = None
            for trip_point in trip_points:
                path = os.path.join(base, trip_point + "_type")
                trip_type = cat(path, fallback='').strip()
                if trip_type == 'critical':
                    critical = bcat(
                        os.path.join(base, trip_point + "_temp"), fallback=None
                    )
                elif trip_type == 'high':
                    high = bcat(
                        os.path.join(base, trip_point + "_temp"), fallback=None
                    )

                if high is not None:
                    try:
                        high = float(high) / 1000.0
                    except ValueError:
                        high = None
                if critical is not None:
                    try:
                        critical = float(critical) / 1000.0
                    except ValueError:
                        critical = None

            ret[unit_name].append(('', current, high, critical))

    return dict(ret)


def sensors_temperatures(fahrenheit=False):
        """Return hardware temperatures. Each entry is a namedtuple
        representing a certain hardware sensor (it may be a CPU, an
        hard disk or something else, depending on the OS and its
        configuration).
        All temperatures are expressed in celsius unless *fahrenheit*
        is set to True.
        """

        def convert(n):
            if n is not None:
                return (float(n) * 9 / 5) + 32 if fahrenheit else n

        ret = collections.defaultdict(list)
        rawdict = __sensors_temperatures()

        for name, values in rawdict.items():
            while values:
                label, current, high, critical = values.pop(0)
                current = convert(current)
                high = convert(high)
                critical = convert(critical)

                if high and not critical:
                    critical = high
                elif critical and not high:
                    high = critical

                ret[name].append(
                    shwtemp(label, current, high, critical)
                )

        return dict(ret)