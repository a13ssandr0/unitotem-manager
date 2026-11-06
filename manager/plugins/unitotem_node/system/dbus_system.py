"""
System DBus helpers: logind (power management) and hostnamed (hostname).

Replaces subprocess calls to systemctl / hostname with proper DBus calls,
which work without elevated privileges (governed by polkit policies).
"""
import os
import re
from contextlib import contextmanager
from socket import gethostname

import sdbus
from sdbus import DbusInterfaceCommonAsync, dbus_method_async, dbus_property_async, dbus_signal_async


# ── Helpers ────────────────────────────────────────────────────────────────

@contextmanager
def _system_bus():
    bus = sdbus.sd_bus_open_system()
    try:
        yield bus
    finally:
        bus.close()


# ── logind ─────────────────────────────────────────────────────────────────

class _Logind(DbusInterfaceCommonAsync, interface_name='org.freedesktop.login1.Manager'):
    """
    Proxy for org.freedesktop.login1.Manager.
    Requires polkit action org.freedesktop.login1.reboot / power-off to be
    allowed for the unitotem user (or set interactive=False for unattended).
    """

    @dbus_method_async('b')
    async def reboot(self, interactive: bool) -> None: ...

    @dbus_method_async('b')
    async def power_off(self, interactive: bool) -> None: ...

    @dbus_method_async(result_signature='s')
    async def can_reboot(self) -> str: ...

    @dbus_method_async(result_signature='s')
    async def can_power_off(self) -> str: ...

    @dbus_method_async('b')
    async def suspend(self, interactive: bool) -> None: ...

    @dbus_method_async(result_signature='s')
    async def can_suspend(self) -> str: ...

    @dbus_signal_async('b')
    def prepare_for_sleep(self) -> bool:
        """True just before the system goes to sleep, False right after it woke up."""
        raise NotImplementedError


def _get_logind(bus) -> _Logind:
    return _Logind.new_proxy('org.freedesktop.login1', '/org/freedesktop/login1', bus)


get_logind = _get_logind        # for code that keeps its own bus open (signal subscriptions)


async def system_reboot():
    with _system_bus() as bus:
        await _get_logind(bus).reboot(False)


async def system_poweroff():
    with _system_bus() as bus:
        await _get_logind(bus).power_off(False)


async def can_reboot() -> bool:
    with _system_bus() as bus:
        return (await _get_logind(bus).can_reboot()) == 'yes'


async def can_poweroff() -> bool:
    with _system_bus() as bus:
        return (await _get_logind(bus).can_power_off()) == 'yes'


async def system_suspend():
    with _system_bus() as bus:
        await _get_logind(bus).suspend(False)


async def can_suspend() -> str:
    """logind's answer, verbatim: 'yes', 'no', 'na' (not supported) or 'challenge' (would need authentication)."""
    with _system_bus() as bus:
        return await _get_logind(bus).can_suspend()


_ETC_HOSTS = '/etc/hosts'
_LOOPBACK = '127.0.1.1'


# ── hostnamed ──────────────────────────────────────────────────────────────

class _Hostnamed(DbusInterfaceCommonAsync, interface_name='org.freedesktop.hostname1'):
    """
    Proxy for org.freedesktop.hostname1 (systemd-hostnamed).
    Changing the static hostname requires polkit action
    org.freedesktop.hostname1.set-static-hostname.
    """

    @dbus_method_async('sb')
    async def set_static_hostname(self, name: str, interactive: bool) -> None: ...

    @dbus_method_async('sb')
    async def set_hostname(self, name: str, interactive: bool) -> None: ...

    @dbus_property_async('s')
    def static_hostname(self) -> str: ...

    @dbus_property_async('s')
    def hostname(self) -> str: ...


def _get_hostnamed(bus) -> _Hostnamed:
    return _Hostnamed.new_proxy('org.freedesktop.hostname1', '/org/freedesktop/hostname1', bus)


async def get_hostname() -> str:
    try:
        with _system_bus() as bus:
            return await _get_hostnamed(bus).static_hostname
    except Exception:
        return gethostname()


async def set_static_hostname(name: str):
    """
    Set the machine's static hostname via systemd-hostnamed, then sync /etc/hosts.

    systemd-hostnamed updates /etc/hostname but intentionally does NOT touch
    /etc/hosts (that is the administrator's responsibility per the man page).
    We do it here so that 127.0.1.1 always resolves to the current hostname.
    """
    name = name.strip()
    if not valid_hostname(name):
        raise ValueError('A host name is letters, digits, dashes and dots, up to 64 characters')
    old = await get_hostname() or gethostname()

    with _system_bus() as bus:
        await _get_hostnamed(bus).set_static_hostname(name, False)

    _sync_etc_hosts(old, name)


_HOSTNAME = re.compile(r'[A-Za-z0-9]([A-Za-z0-9.-]{0,62}[A-Za-z0-9])?')


def valid_hostname(name: str) -> bool:
    """What goes into /etc/hosts as a line of fields: no space, no line break, nothing but name characters."""
    return isinstance(name, str) and _HOSTNAME.fullmatch(name) is not None and '..' not in name


def render_hosts(content: str, new_hostname: str, old_hostname: str = '') -> str:
    """
    /etc/hosts with exactly one `127.0.1.1` entry, for the new hostname. Debian's convention: the machine's own name
    resolves to 127.0.1.1 (not to 127.0.0.1, which is `localhost`). Every other line is kept as it is, except that the
    old name is removed from the aliases of the loopback lines (a stale alias would still resolve to this machine).
    """
    if not valid_hostname(new_hostname):
        raise ValueError('Not a host name')
    lines, placed = [], False
    for line in content.splitlines():
        fields = line.split('#', 1)[0].split()
        if fields and fields[0] == _LOOPBACK:
            if not placed:
                lines.append(f'{_LOOPBACK}\t{new_hostname}')
                placed = True
            continue
        if old_hostname and old_hostname != new_hostname and fields and fields[0] in ('127.0.0.1', '::1'):
            kept = [f for f in fields[1:] if f != old_hostname]
            if len(kept) != len(fields) - 1:
                line = f'{fields[0]}\t{" ".join(kept)}'
        lines.append(line)
    if not placed:
        # after the IPv4 localhost line when there is one, else at the top
        at = next((n + 1 for n, l in enumerate(lines) if l.split()[:1] == ['127.0.0.1']), 0)
        lines.insert(at, f'{_LOOPBACK}\t{new_hostname}')
    return '\n'.join(lines) + '\n'


def _sync_etc_hosts(old_hostname: str, new_hostname: str, path: str = _ETC_HOSTS) -> bool:
    """Write /etc/hosts for the new hostname (atomically, same owner and mode). False when it could not be done."""
    from loguru import logger
    try:
        st = os.stat(path)
        with open(path) as fh:
            content = fh.read()
        new_content = render_hosts(content, new_hostname, old_hostname)
        if new_content == content:
            return True
        tmp = f'{path}.unitotem-tmp'
        with open(tmp, 'w') as fh:
            fh.write(new_content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        # the hostname itself is set by hostnamed; only the local resolution of the new name is stale
        logger.error('Cannot update {} for the hostname {}: {}', path, new_hostname, exc)
        return False


async def watch_hostname(on_change=None):
    """
    Keep /etc/hosts right whoever changes the hostname (this UI, `hostnamectl`, a script): hostnamed announces it with
    a PropertiesChanged signal, so nothing polls. Also heals a mismatch present at start-up (an old release, or a
    change made while the manager was not running). `on_change(new_hostname)` is awaited after each change.
    """
    from loguru import logger
    previous = gethostname()
    _sync_etc_hosts(previous, previous)
    bus = sdbus.sd_bus_open_system()
    proxy = _get_hostnamed(bus)
    async for _interface, changed, _invalidated in proxy.properties_changed:
        if not {'Hostname', 'StaticHostname'} & set(changed):
            continue
        current = gethostname()
        if current == previous:
            continue
        logger.info('Hostname changed: {} -> {}', previous, current)
        _sync_etc_hosts(previous, current)
        previous = current
        if on_change is not None:
            try:
                await on_change(current)
            except Exception:
                logger.exception('Hostname change callback failed')


# ── timedated ──────────────────────────────────────────────────────────────

class _Timedated(DbusInterfaceCommonAsync, interface_name='org.freedesktop.timedate1'):
    """
    Proxy for org.freedesktop.timedate1 (systemd-timedated).
    SetTimezone and SetNTP need polkit action org.freedesktop.timedate1.set-timezone / set-ntp.
    """

    @dbus_method_async(result_signature='as', method_name='ListTimezones')
    async def list_timezones(self) -> list[str]: ...

    @dbus_method_async('sb', method_name='SetTimezone')
    async def set_timezone(self, timezone: str, interactive: bool) -> None: ...

    @dbus_method_async('bb', method_name='SetNTP')
    async def set_ntp(self, enabled: bool, interactive: bool) -> None: ...

    @dbus_property_async('s', property_name='Timezone')
    def timezone(self) -> str: ...

    @dbus_property_async('b', property_name='NTP')
    def ntp(self) -> bool: ...

    @dbus_property_async('b', property_name='CanNTP')
    def can_ntp(self) -> bool: ...

    @dbus_property_async('b', property_name='NTPSynchronized')
    def ntp_synchronized(self) -> bool: ...

    @dbus_property_async('b', property_name='LocalRTC')
    def local_rtc(self) -> bool: ...

    @dbus_property_async('t', property_name='TimeUSec')
    def time_usec(self) -> int: ...


def _get_timedated(bus) -> _Timedated:
    return _Timedated.new_proxy('org.freedesktop.timedate1', '/org/freedesktop/timedate1', bus)


# Zone names of the system's zoneinfo: Z lines are zones (name in field 2), L lines are links (name in field 3).
ZONEINFO_INDEX = '/usr/share/zoneinfo/tzdata.zi'
ZONE_TAB = '/usr/share/zoneinfo/zone1970.tab'
_ZONE_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*')


def _zones_from_index(index: str = ZONEINFO_INDEX, tab: str = ZONE_TAB) -> set[str]:
    """Zone names from the zoneinfo index; zone1970.tab (the third column) when the index is not there."""
    try:
        zones = set()
        with open(index, encoding='utf-8') as fh:
            for line in fh:
                fields = line.split()
                if fields[:1] == ['Z'] and len(fields) > 1:
                    zones.add(fields[1])
                elif fields[:1] == ['L'] and len(fields) > 2:
                    zones.add(fields[2])
        return zones
    except OSError:
        with open(tab, encoding='utf-8') as fh:
            return {f[2] for f in (l.split('\t') for l in fh if not l.startswith('#')) if len(f) > 2}


async def list_timezones() -> list[str]:
    """Every zone name timedated accepts (links included), sorted; UTC always present."""
    from loguru import logger
    try:
        with _system_bus() as bus:
            zones = set(await _get_timedated(bus).list_timezones())
    except Exception as exc:
        logger.warning('timedated ListTimezones failed ({}), reading the zoneinfo index', exc)
        zones = _zones_from_index()
    return sorted({z for z in zones if _ZONE_NAME.fullmatch(z)} | {'UTC'})


def _reload_zone():
    """The process caches the zone at its first use: a TZ override would hide /etc/localtime (the link timedated
    writes), so it is dropped and the zone read again."""
    import time
    os.environ.pop('TZ', None)
    time.tzset()


async def timedate_status() -> dict:
    with _system_bus() as bus:
        proxy = _get_timedated(bus)
        return {
            'timezone': await proxy.timezone,
            'ntp': await proxy.ntp,
            'can_ntp': await proxy.can_ntp,
            'synchronized': await proxy.ntp_synchronized,
        }


async def set_timezone(tz: str):
    """Set the system time zone (the /etc/localtime link, via timedated) and make this process follow it."""
    if not isinstance(tz, str) or not _ZONE_NAME.fullmatch(tz) or tz not in await list_timezones():
        raise ValueError('Unknown time zone')
    with _system_bus() as bus:
        await _get_timedated(bus).set_timezone(tz, False)
    _reload_zone()


async def set_ntp(enabled: bool):
    with _system_bus() as bus:
        proxy = _get_timedated(bus)
        if not await proxy.can_ntp:
            raise ValueError('This node has no network time service to switch')
        await proxy.set_ntp(bool(enabled), False)


async def watch_timedate(on_change=None):
    """
    Follow the time zone and NTP state whoever changes them (this UI, `timedatectl`): timedated announces a change with
    PropertiesChanged, so nothing polls. A new zone reaches this process here; `on_change()` is awaited after each
    relevant change.
    """
    from loguru import logger
    bus = sdbus.sd_bus_open_system()
    proxy = _get_timedated(bus)
    async for _interface, changed, _invalidated in proxy.properties_changed:
        if not {'Timezone', 'NTP', 'NTPSynchronized', 'CanNTP'} & set(changed):
            continue
        if 'Timezone' in changed:
            logger.info('Time zone changed')
            _reload_zone()
        if on_change is not None:
            try:
                await on_change()
            except Exception:
                logger.exception('Date and time change callback failed')
