import re
from asyncio import run, sleep
from os import environ
from socket import gethostname
from uuid import uuid4

import sdbus_async.networkmanager.exceptions
from sdbus import SdBusBaseError
from dotenv import load_dotenv, set_key
from loguru import logger
from qrcode import ERROR_CORRECT_Q, make as make_qr
from qrcode.image.svg import SvgPathFillImage
from sdbus_async.networkmanager import (ActiveConnection, NetworkConnectionSettings, NetworkDeviceWireless,
                                        NetworkManager, NetworkManagerSettings, NmDeviceNotActiveError,
                                        NmSettingsInvalidConnectionError)

from utils import constants as const
from unitotem_node.system.network.misc import get_default_wireless
from unitotem_node.system.network.wifi import SSID_MAX_BYTES, check_passphrase, check_ssid, open_system_bus

load_dotenv(const.envfile)

if 'hotspot_uuid' not in environ:
    environ['hotspot_uuid'] = str(uuid4())
    const.envfile.touch(mode=0o600)
    set_key(const.envfile, 'hotspot_uuid', environ['hotspot_uuid'])


def _default_ssid() -> str:
    """The hostname as SSID, cut to 32 bytes at a character boundary (a hostname can be longer)."""
    return gethostname().encode()[:SSID_MAX_BYTES].decode('utf-8', 'ignore')


async def start_hotspot(ssid: str = None, password: str = None):
    """Starts the hotspot with the given values, the stored ones, or the defaults. Raises InvalidWifiSettings when a given
    SSID or passphrase is out of range, before anything is read or written."""
    if ssid is not None:
        check_ssid(ssid)
    if password is not None:
        check_passphrase(password)
    try:
        with open_system_bus() as bus:
            logger.info('Starting hotspot')
            conn_uuid = environ['hotspot_uuid']

            iw = get_default_wireless()
            if iw is None:
                raise RuntimeError('No wifi device found')

            nm = NetworkManager(bus)
            nm_settings = NetworkManagerSettings(bus)

            if not await nm.wireless_hardware_enabled:
                raise RuntimeError('Wireless hardware not enabled')

            if not await nm.wireless_enabled:
                logger.info('Wireless is not enabled')
                await nm.wireless_enabled.set_async(True)
                await sleep(2)

            if await nm.wireless_enabled:
                logger.success('Wireless enabled')
            else:
                raise RuntimeError('Wireless enabling failed')

            devpath = await nm.get_device_by_ip_iface(iw)
            netdev = NetworkDeviceWireless(devpath, bus)

            try:
                connection_path = await nm_settings.get_connection_by_uuid(conn_uuid)
                logger.info('Found existing hotspot connection {} on {}', connection_path, await netdev.interface)
                nc = NetworkConnectionSettings(connection_path, bus)

                try:
                    s_ssid: str = (await nc.get_settings())['802-11-wireless']['ssid'][1].decode()
                    s_pwd: str = (await nc.get_secrets('802-11-wireless-security'))['802-11-wireless-security']['psk'][1]
                except:
                    # in case anything goes wrong retrieving stored values discard all and start over
                    logger.error('An error occurred while trying to retrieve ssid and/or password, recreating hotspot')
                    conn_dbus_path = await nm_settings.get_connection_by_uuid(environ['hotspot_uuid'])
                    await NetworkConnectionSettings(conn_dbus_path, bus).delete()
                    raise NmSettingsInvalidConnectionError()

                if s_ssid == ssid and s_pwd == password:
                    pass
                elif ssid is None and password is None:
                    ssid = s_ssid
                    password = s_pwd
                else:
                    logger.info('Recreating hotspot with new ssid/password')
                    conn_dbus_path = await nm_settings.get_connection_by_uuid(environ['hotspot_uuid'])
                    await NetworkConnectionSettings(conn_dbus_path, bus).delete()
                    raise NmSettingsInvalidConnectionError()
            except NmSettingsInvalidConnectionError:

                if ssid is None:
                    ssid = _default_ssid()
                    check_ssid(ssid)                    # an empty hostname
                if password is None:
                    password = (await netdev.hw_address).replace(':', '').upper()[-8:]

                # noinspection PyTypeChecker
                connection_path = await nm_settings.add_connection({
                    "connection"              : {
                        "type": ('s', "802-11-wireless"),
                        "uuid": ('s', conn_uuid),
                        "id"  : ('s', "UniTotem first boot hotspot"),
                    },
                    "802-11-wireless"         : {
                        "ssid"   : ('ay', ssid.encode()),
                        "mode"   : ('s', "ap"),
                        "band"   : ('s', "bg"),
                        "channel": ('u', 1),
                    },
                    "802-11-wireless-security": {
                        "key-mgmt": ('s', "wpa-psk"),
                        "psk"     : ('s', password)
                    },
                    "ipv4"                    : {
                        "method": ('s', "shared")
                    },
                    "ipv6"                    : {
                        "method": ('s', "ignore")
                    },
                })

            await nm.activate_connection(connection_path, devpath)
            return ssid, password
    except sdbus_async.networkmanager.exceptions.NetworkManagerPermissionDeniedError:
        logger.error("NetworkManager dbus permission denied")
        return "", ""



async def _hotspot_is_active(bus, nm, settings_path: str) -> bool:
    """Whether the connection with this settings object is one of the active connections (their own paths are other objects)."""
    for active_path in await nm.active_connections:
        try:
            if await ActiveConnection(active_path, bus).connection == settings_path:
                return True
        except (sdbus_async.networkmanager.exceptions.NetworkManagerBaseError, SdBusBaseError):
            continue                   # it went away while looking
    return False


async def is_hotspot_enabled():
    try:
        with open_system_bus() as bus:
            nm = NetworkManager(bus)
            if not await nm.wireless_enabled:
                return False

            try:
                connection_path = await NetworkManagerSettings(bus).get_connection_by_uuid(environ['hotspot_uuid'])
                return await _hotspot_is_active(bus, nm, connection_path)
            except NmSettingsInvalidConnectionError:
                return False
    except sdbus_async.networkmanager.exceptions.NetworkManagerPermissionDeniedError:
        logger.error("NetworkManager dbus permission denied")
        return False



async def get_hotspot():
    try:
        with open_system_bus() as bus:
            connection_path = await NetworkManagerSettings(bus).get_connection_by_uuid(environ['hotspot_uuid'])
            logger.info('Getting hotspot connection {}', connection_path)

            nc = NetworkConnectionSettings(connection_path, bus)

            stored_ssid: str = (await nc.get_settings())['802-11-wireless']['ssid'][1].decode()
            stored_pwd: str = (await nc.get_secrets('802-11-wireless-security'))['802-11-wireless-security']['psk'][1]
            return stored_ssid, stored_pwd
    except sdbus_async.networkmanager.exceptions.NetworkManagerPermissionDeniedError:
        logger.error("NetworkManager dbus permission denied")
        return "", ""


async def get_hotspot_with_qr():
    ssid, password = await get_hotspot()
    return {'ssid': ssid, 'password': password, 'qrcode': wifi_qr(ssid, password)}


async def stop_hotspot():
    """Takes the fallback hotspot down and removes its profile. This also runs whenever the manager stops, so it must touch
    nothing but the hotspot: the Wi-Fi device is disconnected only while it is the hotspot that is active on it."""
    try:
        with open_system_bus() as bus:
            nm = NetworkManager(bus)

            if not await nm.wireless_enabled:
                return

            try:
                conn_dbus_path = await NetworkManagerSettings(bus).get_connection_by_uuid(environ['hotspot_uuid'])
            except NmSettingsInvalidConnectionError:
                logger.info('No hotspot connection: nothing to stop')
                return

            if await _hotspot_is_active(bus, nm, conn_dbus_path):
                iface = get_default_wireless()
                if iface is not None:
                    logger.info('Stopping hotspot')
                    netdev = NetworkDeviceWireless(await nm.get_device_by_ip_iface(iface), bus)
                    try:
                        await netdev.disconnect()
                        logger.info('Stopped hotspot on interface {}', await netdev.interface)
                    except NmDeviceNotActiveError:
                        logger.info('Hotspot was already stopped')

            await NetworkConnectionSettings(conn_dbus_path, bus).delete()
            logger.info('Deleted hotspot connection')
    except sdbus_async.networkmanager.exceptions.NetworkManagerPermissionDeniedError:
        logger.error("NetworkManager dbus permission denied")


def wifi_qr(ssid, passwd):
    # noinspection PyTypeChecker
    return re.sub(r'(?:width|height)=\".*?mm"', '',
                  make_qr(data=f'WIFI:S:{ssid};T:WPA;P:{passwd};;',
                          error_correction=ERROR_CORRECT_Q,
                          image_factory=SvgPathFillImage).to_string().decode())


if __name__ == '__main__':
    async def main():
        await start_hotspot()
        await sleep(5)
        ssid, pasw = await get_hotspot()
        print(ssid, pasw)
        await stop_hotspot()


    run(main())
