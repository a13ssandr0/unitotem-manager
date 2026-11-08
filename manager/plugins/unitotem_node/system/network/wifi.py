import re
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


class InvalidWifiSettings(ValueError):
    """SSID or passphrase outside the 802.11 limits; raised before anything is written to NetworkManager."""


SSID_MAX_BYTES = 32
PASSPHRASE_MIN, PASSPHRASE_MAX = 8, 63
_RAW_PSK = re.compile(r'[0-9A-Fa-f]{64}')


def check_ssid(ssid) -> None:
    """An SSID is 1 to 32 bytes (not characters)."""
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= SSID_MAX_BYTES:
        raise InvalidWifiSettings(f'SSID must be 1 to {SSID_MAX_BYTES} bytes')


def check_passphrase(password, allow_raw_key: bool = False) -> None:
    """A WPA passphrase is 8 to 63 characters; with `allow_raw_key` a 64 hex digit pre-shared key is accepted too."""
    if not isinstance(password, str):
        raise InvalidWifiSettings('passphrase must be text')
    if allow_raw_key and _RAW_PSK.fullmatch(password):
        return
    if not PASSPHRASE_MIN <= len(password) <= PASSPHRASE_MAX:
        raise InvalidWifiSettings(f'passphrase must be {PASSPHRASE_MIN} to {PASSPHRASE_MAX} characters')


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
            logger.info('Wireless is not enabled')
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

        # an SSID is any bytes (802.11): a name that is not UTF-8 is shown with replacement characters, the list stays whole
        for ap in aps: ap['ssid'] = ap['ssid'].decode('utf-8', 'replace')

        return aps


async def scan_access_points():
    with open_system_bus() as bus:
        nm = NetworkManager(bus)

        if not await nm.wireless_hardware_enabled:
            raise RuntimeError('Wireless hardware not enabled')

        if not await nm.wireless_enabled:
            logger.info('Wireless is not enabled')
            await nm.wireless_enabled.set_async(True)
            await sleep(2)

        for device_path in await nm.get_devices():
            device = NetworkDeviceWireless(device_path, bus)
            if await device.device_type == DeviceType.WIFI:
                logger.info('Triggering scan on {}', await device.interface)
                await device.request_scan({})
