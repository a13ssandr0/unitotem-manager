from asyncio import sleep
from contextlib import contextmanager

import sdbus
from loguru import logger
from sdbus_async.networkmanager import AccessPoint, DeviceType, NetworkDeviceWireless, NetworkManager


@contextmanager
def open_system_bus():
    bus = sdbus.sd_bus_open_system()
    try:
        yield bus
    finally:
        bus.close()


async def has_wireless():
    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        return await nm.wireless_hardware_enabled


async def is_wireless_enabled():
    with open_system_bus() as bus:
        nm = NetworkManager(bus)

        if not await nm.wireless_hardware_enabled:
            raise RuntimeError('Wireless hardware not enabled')

        return await nm.wireless_enabled

async def set_wireless_enabled(status):
    with open_system_bus() as bus:
        nm = NetworkManager(bus)

        if not await nm.wireless_hardware_enabled:
            raise RuntimeError('Wireless hardware not enabled')

        await nm.wireless_enabled.set_async(status)


async def get_access_points():
    with open_system_bus() as bus:
        nm = NetworkManager(bus)

        if not await nm.wireless_hardware_enabled:
            raise RuntimeError('Wireless hardware not enabled')

        if not await nm.wireless_enabled:
            logger.info('Wireless not yet enabled')
            await nm.wireless_enabled.set_async(True)

        aps = set()

        for device_path in await nm.get_devices():
            device = NetworkDeviceWireless(device_path, bus)
            if await device.device_type == DeviceType.WIFI:
                logger.info('Getting access points discovered by {}', await device.interface)
                aps.update(await device.get_all_access_points())

        logger.info('{} access points discovered', len(aps))

        aps = sorted([await AccessPoint(ap, bus).properties_get_all_dict('reuse') for ap in aps],
                     key=lambda ap: ap['strength'], reverse=True)

        for ap in aps: ap['ssid'] = ap['ssid'].decode()

        return aps


async def scan_access_points():
    with open_system_bus() as bus:
        nm = NetworkManager(bus)

        if not await nm.wireless_hardware_enabled:
            raise RuntimeError('Wireless hardware not enabled')

        if not await nm.wireless_enabled:
            logger.info('Wireless not yet enabled')
            await nm.wireless_enabled.set_async(True)
            await sleep(2)

        for device_path in await nm.get_devices():
            device = NetworkDeviceWireless(device_path, bus)
            if await device.device_type == DeviceType.WIFI:
                logger.info('Triggering scan on {}', await device.interface)
                await device.request_scan({})
