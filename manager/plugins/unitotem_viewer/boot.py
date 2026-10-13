"""Talking to systemd about the boot splash, through its D-Bus API (no systemctl)."""
import asyncio
import os

from loguru import logger

SPLASH_UNIT = 'unitotem-splash.service'


def stop_boot_splash():
    """Ends the separate splash process the boot started (see splash.py), if there is one. Without systemd (a development
    run) or without permission this does nothing."""
    if not os.path.isdir('/run/systemd/system'):
        return
    try:
        asyncio.run(_stop_unit(SPLASH_UNIT))
    except Exception as exc:
        logger.warning('Cannot stop {}: {}', SPLASH_UNIT, exc)


async def _stop_unit(name: str):
    import sdbus
    from sdbus import DbusInterfaceCommonAsync, dbus_method_async

    class SystemdManager(DbusInterfaceCommonAsync, interface_name='org.freedesktop.systemd1.Manager'):
        @dbus_method_async('ss', 'o')
        async def stop_unit(self, name: str, mode: str) -> str: ...

    bus = sdbus.sd_bus_open_system()
    try:
        await SystemdManager.new_proxy('org.freedesktop.systemd1', '/org/freedesktop/systemd1', bus).stop_unit(
            name, 'replace')
    finally:
        bus.close()
