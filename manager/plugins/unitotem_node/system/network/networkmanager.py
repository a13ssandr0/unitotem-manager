"""
Full NetworkManager DBus wrapper using sdbus_async.networkmanager.

Exposes high-level helpers for connection management, device state, and IP
information — everything needed to replace the old netplan-based API.
"""
import ipaddress
import re
import uuid as _uuid_mod
from contextlib import contextmanager
from typing import Optional

import sdbus
from sdbus_async.networkmanager import (
    NetworkConnectionSettings,
    NetworkDeviceWireless,
    NetworkManager,
    NetworkManagerSettings,
    DeviceType,
    DeviceState,
)

from unitotem_node.system.network.wifi import InvalidWifiSettings, check_passphrase, check_ssid

try:
    from sdbus_async.networkmanager import NetworkDeviceGeneric
except ImportError:
    # Older versions of sdbus-networkmanager may not export this
    NetworkDeviceGeneric = None  # type: ignore

# The classes are called ActiveConnection and IPv4Config in sdbus-networkmanager (the long NetworkManager...
# names are the interface mixins, not usable objects). Importing names that do not exist was silently swallowed here
# and made every device look like it had no active connection and no address.
try:
    from sdbus_async.networkmanager import ActiveConnection as NetworkManagerConnectionActive
except ImportError:
    NetworkManagerConnectionActive = None  # type: ignore

try:
    from sdbus_async.networkmanager import IPv4Config as NetworkManagerIP4Config
except ImportError:
    NetworkManagerIP4Config = None  # type: ignore

if NetworkManagerConnectionActive is None or NetworkManagerIP4Config is None:
    from loguru import logger as _logger
    _logger.error('sdbus_async.networkmanager has no ActiveConnection/IPv4Config: devices will show no address')

try:
    from sdbus_async.networkmanager import NetworkDeviceWired
except ImportError:
    NetworkDeviceWired = None  # type: ignore


# ── helpers ───────────────────────────────────────────────────────────────

@contextmanager
def open_system_bus():
    bus = sdbus.sd_bus_open_system()
    try:
        yield bus
    finally:
        bus.close()


def _unpack(v):
    """sdbus returns DBus variant values as (type_sig, value); unwrap to just value."""
    if isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str):
        return v[1]
    return v


def _json_safe(v):
    """Settings values as the page can read them: variants unwrapped at any depth, byte arrays (an SSID, a BSSID, a MAC address) as lists of numbers."""
    v = _unpack(v)
    if isinstance(v, (bytes, bytearray)):
        return list(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def _unpack_section(d: dict) -> dict:
    return {k: _json_safe(v) for k, v in d.items()}


def _device_for_path(path: str, bus, dtype_val):
    """Return the right device proxy class for a given device type integer."""
    if dtype_val == 1 and NetworkDeviceWired:            # NM_DEVICE_TYPE_ETHERNET
        return NetworkDeviceWired(path, bus)
    if dtype_val == 2:                                   # NM_DEVICE_TYPE_WIFI
        return NetworkDeviceWireless(path, bus)
    if NetworkDeviceGeneric:
        return NetworkDeviceGeneric(path, bus)
    return NetworkDeviceWireless(path, bus)              # best-effort fallback


# ── connection listing & details ──────────────────────────────────────────

async def get_all_connections() -> list[dict]:
    """Return a lightweight list of all saved connection profiles."""
    with open_system_bus() as bus:
        nm_settings = NetworkManagerSettings(bus)
        paths = await nm_settings.list_connections()
        result = []
        for path in paths:
            nc = NetworkConnectionSettings(path, bus)
            try:
                s = await nc.get_settings()
                c = _unpack_section(s.get('connection', {}))
                result.append({
                    'path'       : path,
                    'id'         : c.get('id', ''),
                    'uuid'       : c.get('uuid', ''),
                    'type'       : c.get('type', ''),
                    'autoconnect': bool(_unpack(c.get('autoconnect', ('b', True)))),
                })
            except Exception:
                continue
        return result


async def get_connection_details(path: str) -> dict:
    """Return full unpacked settings for one connection profile."""
    with open_system_bus() as bus:
        nc = NetworkConnectionSettings(path, bus)
        raw = await nc.get_settings()
        return {section: _unpack_section(values) for section, values in raw.items()}


async def get_connection_by_uuid(conn_uuid: str) -> Optional[str]:
    """Return object path for a connection UUID, or None if not found."""
    with open_system_bus() as bus:
        nm_settings = NetworkManagerSettings(bus)
        try:
            return await nm_settings.get_connection_by_uuid(conn_uuid)
        except Exception:
            return None


# ── active connections & devices ──────────────────────────────────────────

async def get_active_connections() -> list[dict]:
    """Return currently active connections with state and device list."""
    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        result = []
        if NetworkManagerConnectionActive is None:
            return result
        for path in await nm.active_connections:
            try:
                ac = NetworkManagerConnectionActive(path, bus)
                state = await ac.state
                result.append({
                    'path'   : path,
                    'id'     : await ac.id,
                    'uuid'   : await ac.uuid,
                    'type'   : await ac.connection_type,
                    'state'  : state.value if hasattr(state, 'value') else int(state),
                    'devices': list(await ac.devices),
                })
            except Exception:
                continue
        return result


async def get_devices() -> list[dict]:
    """Return all network devices with state, type, IP, and active connection."""
    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        result = []

        for dev_path in await nm.get_all_devices():
            try:
                # Use generic proxy first to read common properties
                dev_proxy = NetworkDeviceWireless(dev_path, bus)
                dtype_raw = await dev_proxy.device_type
                dtype_val = dtype_raw.value if hasattr(dtype_raw, 'value') else int(dtype_raw)
                dstate_raw = await dev_proxy.state
                dstate_val = dstate_raw.value if hasattr(dstate_raw, 'value') else int(dstate_raw)

                dev_info: dict = {
                    'path'             : dev_path,
                    'interface'        : await dev_proxy.interface,
                    'type'             : dtype_val,
                    'state'            : dstate_val,
                    'hw_address'       : '',
                    'active_connection': None,
                    'ip4'              : None,
                }

                try:
                    dev_info['hw_address'] = await dev_proxy.hw_address
                except Exception:
                    pass

                active_path = await dev_proxy.active_connection
                if active_path and active_path != '/' and NetworkManagerConnectionActive:
                    try:
                        ac = NetworkManagerConnectionActive(active_path, bus)
                        ac_type = await ac.connection_type
                        dev_info['active_connection'] = {
                            'path': active_path,
                            'id'  : await ac.id,
                            'uuid': await ac.uuid,
                            'type': ac_type,
                        }
                    except Exception:
                        pass

                try:
                    ip4_path = await dev_proxy.ip4_config
                    if ip4_path and ip4_path != '/' and NetworkManagerIP4Config:
                        ip4 = NetworkManagerIP4Config(ip4_path, bus)
                        addrs = await ip4.address_data
                        gateway = ''
                        try:
                            gateway = await ip4.gateway
                        except Exception:
                            pass
                        dns_list: list[str] = []
                        try:
                            for ns in (await ip4.nameservers or []):
                                # NM returns DNS as 32-bit uint in network byte order
                                if isinstance(ns, int):
                                    octets = ns.to_bytes(4, 'little')
                                    dns_list.append('.'.join(str(b) for b in octets))
                                else:
                                    dns_list.append(str(ns))
                        except Exception:
                            pass
                        dev_info['ip4'] = {
                            'addresses': [
                                {
                                    'address': _unpack(a.get('address', ('s', ''))),
                                    'prefix' : _unpack(a.get('prefix', ('u', 24))),
                                }
                                for a in addrs
                            ],
                            'gateway': gateway,
                            'dns'    : dns_list,
                        }
                except Exception:
                    pass

                result.append(dev_info)
            except Exception:
                continue

        return result


# ── activate / deactivate ─────────────────────────────────────────────────

async def activate_connection(conn_path: str, dev_path: str) -> str:
    """Activate a connection on a device; returns the active-connection path."""
    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        return await nm.activate_connection(conn_path, dev_path, '/')


async def deactivate_connection(active_path: str):
    """Deactivate an active connection by its active-connection object path."""
    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        await nm.deactivate_connection(active_path)


# ── delete ────────────────────────────────────────────────────────────────

async def delete_connection(conn_path: str):
    with open_system_bus() as bus:
        nc = NetworkConnectionSettings(conn_path, bus)
        await nc.delete()


# ── add / update connections ──────────────────────────────────────────────

async def add_connection(settings: dict) -> str:
    """Add a new connection profile and return its object path."""
    with open_system_bus() as bus:
        nm_settings = NetworkManagerSettings(bus)
        return await nm_settings.add_connection(settings)


async def update_connection(conn_path: str, settings: dict):
    """Update an existing connection profile."""
    with open_system_bus() as bus:
        nc = NetworkConnectionSettings(conn_path, bus)
        await nc.update(settings)


IP_METHODS = ('auto', 'manual', 'link-local', 'disabled')


class InvalidIPSettings(ValueError):
    """IPv4 settings that cannot be applied; raised before anything is written to NetworkManager."""


def _ipv4_address(value, what: str) -> bytes:
    """The four bytes (network order) of an IPv4 address typed by a user."""
    try:
        if not isinstance(value, str):
            raise ValueError
        return ipaddress.IPv4Address(value).packed
    except ValueError:
        raise InvalidIPSettings(f'{value!r} is not an IPv4 {what}') from None


def _build_ipv4_section(method: str, address: Optional[str],
                         prefix: int, gateway: Optional[str],
                         dns: Optional[list[str]]) -> dict:
    """Build the ipv4 section of NM connection settings. Invalid values raise InvalidIPSettings."""
    if method not in IP_METHODS:
        raise InvalidIPSettings(f'{method!r} is not one of {list(IP_METHODS)}')
    if method != 'manual':
        return {'method': ('s', method)}
    if not address:
        raise InvalidIPSettings('static configuration needs an address')
    _ipv4_address(address, 'address')
    if isinstance(prefix, bool) or not isinstance(prefix, int) or not 1 <= prefix <= 32:
        raise InvalidIPSettings(f'{prefix!r} is not a prefix length (1-32)')
    if gateway:
        _ipv4_address(gateway, 'gateway')
    dns_uints = []
    for d in (dns or []):
        # NM wants the address bytes in memory order, i.e. a little-endian uint32 on every machine we run on
        dns_uints.append(int.from_bytes(_ipv4_address(d, 'DNS server'), 'little'))
    return {
        'method'      : ('s', 'manual'),
        'address-data': ('aa{sv}', [
            {'address': ('s', address), 'prefix': ('u', prefix)}
        ]),
        'gateway'     : ('s', gateway or ''),
        'dns'         : ('au', dns_uints),
    }


async def connect_wifi(ssid: str,
                       password: Optional[str],
                       device_iface: str,
                       bssid: Optional[str] = None,
                       conn_uuid: Optional[str] = None,
                       ip_method: str = 'auto',
                       ip_address: Optional[str] = None,
                       prefix_len: int = 24,
                       gateway: Optional[str] = None,
                       dns: Optional[list[str]] = None) -> str:
    """
    Add (or update) a WiFi connection and immediately activate it.
    Returns the connection UUID. Raises InvalidWifiSettings before anything is written if the SSID or passphrase is out of range.
    """
    check_ssid(ssid)
    if password:
        check_passphrase(password, allow_raw_key=True)
    if bssid and not re.fullmatch(r'[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}', bssid):
        raise InvalidWifiSettings(f'{bssid!r} is not a BSSID')

    uid = conn_uuid or str(_uuid_mod.uuid4())

    settings: dict = {
        'connection': {
            'type': ('s', '802-11-wireless'),
            'uuid': ('s', uid),
            'id'  : ('s', ssid),
        },
        '802-11-wireless': {
            'ssid': ('ay', ssid.encode()),
            'mode': ('s', 'infrastructure'),
        },
        'ipv4': _build_ipv4_section(ip_method, ip_address, prefix_len, gateway, dns),
        'ipv6': {'method': ('s', 'auto')},
    }

    if bssid:
        settings['802-11-wireless']['bssid'] = ('ay', bytes(int(x, 16) for x in bssid.split(':')))

    if password:
        settings['802-11-wireless-security'] = {
            'key-mgmt': ('s', 'wpa-psk'),
            'psk'     : ('s', password),
        }

    with open_system_bus() as bus:
        nm = NetworkManager(bus)
        nm_settings = NetworkManagerSettings(bus)

        dev_path = await nm.get_device_by_ip_iface(device_iface)

        existing = await get_connection_by_uuid(uid)
        if existing:
            nc = NetworkConnectionSettings(existing, bus)
            await nc.update(settings)
            conn_path = existing
        else:
            conn_path = await nm_settings.add_connection(settings)

        await nm.activate_connection(conn_path, dev_path, '/')

    return uid


async def edit_connection(conn_path: str,
                          conn_id: Optional[str] = None,
                          autoconnect: Optional[bool] = None,
                          ip_method: str = 'auto',
                          ip_address: Optional[str] = None,
                          prefix_len: int = 24,
                          gateway: Optional[str] = None,
                          dns: Optional[list[str]] = None,
                          ssid: Optional[str] = None,
                          password: Optional[str] = None,
                          unlock_bssid: bool = False):
    """
    Partially update a connection's settings.
    Only the provided fields are changed; others are left untouched.
    """
    if ssid is not None:
        check_ssid(ssid)
    if password is not None:
        check_passphrase(password, allow_raw_key=True)

    with open_system_bus() as bus:
        nc = NetworkConnectionSettings(conn_path, bus)
        raw = await nc.get_settings()

        conn_section = dict(raw.get('connection', {}))
        if conn_id is not None:
            conn_section['id'] = ('s', conn_id)
        if autoconnect is not None:
            conn_section['autoconnect'] = ('b', autoconnect)

        ipv4 = _build_ipv4_section(ip_method, ip_address, prefix_len, gateway, dns)
        new = dict(raw)
        new['connection'] = conn_section
        new['ipv4'] = ipv4

        if ssid is not None:
            wifi = dict(raw.get('802-11-wireless', {}))
            wifi['ssid'] = ('ay', ssid.encode())
            new['802-11-wireless'] = wifi

        if unlock_bssid:
            # a profile tied to one access point never moves to a stronger one: drop the tie
            wifi = dict(new.get('802-11-wireless', raw.get('802-11-wireless', {})))
            wifi.pop('bssid', None)
            new['802-11-wireless'] = wifi

        if password is not None:
            sec = dict(raw.get('802-11-wireless-security', {}))
            sec['psk'] = ('s', password)
            new['802-11-wireless-security'] = sec

        await nc.update(new)
