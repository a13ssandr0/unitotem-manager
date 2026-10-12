"""
System DBus helpers: logind (power management) and hostnamed (hostname).

Replaces subprocess calls to systemctl / hostname with proper DBus calls,
which work without elevated privileges (governed by polkit policies).
"""
from contextlib import contextmanager
from socket import gethostname

import sdbus
from sdbus import DbusInterfaceCommonAsync, dbus_method_async, dbus_property_async


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


def _get_logind(bus) -> _Logind:
    return _Logind.new_proxy('org.freedesktop.login1', '/org/freedesktop/login1', bus)


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
    name = name.strip()[:64]
    old = await get_hostname()

    with _system_bus() as bus:
        await _get_hostnamed(bus).set_static_hostname(name, False)

    _sync_etc_hosts(old, name)


def _sync_etc_hosts(old_hostname: str, new_hostname: str):
    """
    Replace the 127.0.1.1 line in /etc/hosts that matches old_hostname.
    Creates the entry if it does not exist yet.
    Requires write access to /etc/hosts (add a sudoers rule or run as root,
    or adjust the unitotem user's /etc/hosts ownership in the installer).
    """
    import re
    _ETC_HOSTS = '/etc/hosts'
    _LOOPBACK   = '127.0.1.1'

    try:
        with open(_ETC_HOSTS, 'r') as fh:
            content = fh.read()

        # Replace any existing 127.0.1.1 line that references old_hostname
        pattern = re.compile(
            r'^' + re.escape(_LOOPBACK) + r'[ \t]+.*' + re.escape(old_hostname) + r'.*$',
            re.MULTILINE,
        )
        new_line = f'{_LOOPBACK}\t{new_hostname}'
        if pattern.search(content):
            new_content = pattern.sub(new_line, content)
        else:
            # No matching line found — append one
            new_content = content.rstrip('\n') + f'\n{new_line}\n'

        with open(_ETC_HOSTS, 'w') as fh:
            fh.write(new_content)
    except PermissionError:
        # /etc/hosts is not writable by the unitotem user — log and continue.
        # The hostname is still set correctly via hostnamed; only local DNS
        # resolution via /etc/hosts will be stale until manually corrected.
        from loguru import logger
        logger.warning(
            '/etc/hosts is not writable; {} → {} entry not updated. '
            'Grant write access or add a sudoers rule if needed.',
            old_hostname, new_hostname,
        )
