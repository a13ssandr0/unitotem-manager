from typing import Optional

from loguru import logger

import utils.system.network.wifi as w
from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models import assets
from utils.models.playlists import playlists_manager
from utils.models.user import UserPerms
from utils.system.network.hotspot import is_hotspot_enabled, stop_hotspot
from utils.system.network.ip import do_ip_addr
from utils.system.network.misc import get_default_wireless


class Settings(WSAPIBase):
    @UserPerms.requires.none
    async def hostname(self):
        from utils.system.dbus_system import get_hostname
        return WSBroadcast(hostname=await get_hostname())

    async def setHostname(self, hostname: str):
        from utils.system.dbus_system import set_static_hostname, get_hostname
        try:
            await set_static_hostname(hostname)
            return WSBroadcast(hostname=await get_hostname())
        except Exception as e:
            logger.error('setHostname failed: {}', e)
            return WSResponse(error=str(e))

    @staticmethod
    def get_default_wlan_device():
        return WSBroadcast(device=get_default_wireless())

    @staticmethod
    async def has_wireless():
        return WSBroadcast(wireless=await w.has_wireless())

    @staticmethod
    async def is_wireless_enabled():
        return WSBroadcast(enabled=await w.is_wireless_enabled())

    async def set_wireless_enabled(self, enabled: bool):
        await w.set_wireless_enabled(enabled)
        return await self.is_wireless_enabled()

    @staticmethod
    async def get_wireless_networks():
        await w.scan_access_points()
        return WSBroadcast(wifis=await w.get_access_points())

    # ── NetworkManager connection/device API ──────────────────────────────

    class NM(WSAPIBase):
        """
        Full NetworkManager management API via DBus.
        Replaces the old Netplan class that used rpyc + root daemon.
        """

        @staticmethod
        async def devices():
            """List all network devices with state, type, IP address, and active connection."""
            from utils.system.network.networkmanager import get_devices
            try:
                return WSBroadcast(devices=await get_devices())
            except Exception as e:
                logger.error('NM.devices failed: {}', e)
                return WSResponse(error=str(e))

        @staticmethod
        async def connections():
            """List all saved connection profiles."""
            from utils.system.network.networkmanager import get_all_connections
            try:
                return WSBroadcast(connections=await get_all_connections())
            except Exception as e:
                logger.error('NM.connections failed: {}', e)
                return WSResponse(error=str(e))

        @staticmethod
        async def activeConnections():
            """List active connections with state."""
            from utils.system.network.networkmanager import get_active_connections
            try:
                return WSBroadcast(active=await get_active_connections())
            except Exception as e:
                logger.error('NM.activeConnections failed: {}', e)
                return WSResponse(error=str(e))

        @staticmethod
        async def connectionDetails(path: str):
            """Return full (unpacked) settings for one connection."""
            from utils.system.network.networkmanager import get_connection_details
            try:
                return WSBroadcast(details=await get_connection_details(path))
            except Exception as e:
                logger.error('NM.connectionDetails failed: {}', e)
                return WSResponse(error=str(e))

        @staticmethod
        async def activate(conn_path: str, dev_path: str):
            """Activate a connection on a device."""
            from utils.system.network.networkmanager import activate_connection, get_devices, get_active_connections
            try:
                await activate_connection(conn_path, dev_path)
                # If we just connected to a network, stop the fallback hotspot
                if await is_hotspot_enabled() and do_ip_addr(True):
                    await stop_hotspot()
                    am = playlists_manager.default
                    if am.current == assets.first_boot:
                        am.next_a()
                yield WSBroadcast(devices=await get_devices())
                yield WSBroadcast(active=await get_active_connections())
            except Exception as e:
                logger.error('NM.activate failed: {}', e)
                yield WSResponse(error=str(e))

        @staticmethod
        async def deactivate(active_path: str):
            """Deactivate an active connection."""
            from utils.system.network.networkmanager import deactivate_connection, get_devices, get_active_connections
            try:
                await deactivate_connection(active_path)
                yield WSBroadcast(devices=await get_devices())
                yield WSBroadcast(active=await get_active_connections())
            except Exception as e:
                logger.error('NM.deactivate failed: {}', e)
                yield WSResponse(error=str(e))

        @staticmethod
        async def connectWifi(ssid: str,
                              device: str,
                              password: Optional[str] = None,
                              bssid: Optional[str] = None,
                              ip_method: str = 'auto',
                              ip_address: Optional[str] = None,
                              prefix_len: int = 24,
                              gateway: Optional[str] = None,
                              dns: Optional[list] = None):
            """
            Add (or update) a WiFi profile and immediately activate it.
            Handles DHCP and static IP configurations.
            """
            from utils.system.network.networkmanager import connect_wifi, get_devices, get_active_connections
            try:
                uid = await connect_wifi(
                    ssid=ssid, password=password,
                    device_iface=device, bssid=bssid,
                    ip_method=ip_method, ip_address=ip_address,
                    prefix_len=prefix_len, gateway=gateway, dns=dns,
                )
                # Stop fallback hotspot once we connected
                if await is_hotspot_enabled() and do_ip_addr(True):
                    await stop_hotspot()
                    am = playlists_manager.default
                    if am.current == assets.first_boot:
                        am.next_a()
                yield WSBroadcast(devices=await get_devices())
                yield WSBroadcast(active=await get_active_connections())
                yield WSBroadcast(connected_uuid=uid)
            except Exception as e:
                logger.error('NM.connectWifi failed: {}', e)
                yield WSResponse(error=str(e))

        @staticmethod
        async def editConnection(conn_path: str,
                                 conn_id: Optional[str] = None,
                                 autoconnect: Optional[bool] = None,
                                 ip_method: str = 'auto',
                                 ip_address: Optional[str] = None,
                                 prefix_len: int = 24,
                                 gateway: Optional[str] = None,
                                 dns: Optional[list] = None,
                                 ssid: Optional[str] = None,
                                 password: Optional[str] = None):
            """Edit an existing connection profile in-place."""
            from utils.system.network.networkmanager import edit_connection, get_all_connections
            try:
                await edit_connection(
                    conn_path=conn_path, conn_id=conn_id,
                    autoconnect=autoconnect,
                    ip_method=ip_method, ip_address=ip_address,
                    prefix_len=prefix_len, gateway=gateway, dns=dns,
                    ssid=ssid, password=password,
                )
                return WSBroadcast(connections=await get_all_connections())
            except Exception as e:
                logger.error('NM.editConnection failed: {}', e)
                return WSResponse(error=str(e))

        @staticmethod
        async def deleteConnection(conn_path: str):
            """Remove a connection profile permanently."""
            from utils.system.network.networkmanager import delete_connection, get_all_connections
            try:
                await delete_connection(conn_path)
                return WSBroadcast(connections=await get_all_connections())
            except Exception as e:
                logger.error('NM.deleteConnection failed: {}', e)
                return WSResponse(error=str(e))
