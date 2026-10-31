"""stop_hotspot runs whenever the manager stops: it must never disconnect a Wi-Fi network that is not the hotspot."""
import asyncio
import contextlib

from sdbus_async.networkmanager import NmSettingsInvalidConnectionError

from plugins.unitotem_node.system.network import hotspot

HOTSPOT = '/settings/hotspot'
HOME = '/settings/home'


class Calls:
    def __init__(self):
        self.disconnected = 0
        self.deleted = []


def install(monkeypatch, *, profile_exists, active_settings):
    """`active_settings`: the settings object of the connection active on the Wi-Fi device (None: nothing is active)."""
    calls = Calls()

    class NM:
        def __init__(self, bus): pass
        @property
        def wireless_enabled(self):
            async def value(): return True
            return value()
        @property
        def active_connections(self):
            async def value(): return ['/active/1'] if active_settings else []
            return value()
        async def get_device_by_ip_iface(self, iface): return '/dev/wlan0'

    class Settings:
        def __init__(self, bus): pass
        async def get_connection_by_uuid(self, uuid):
            if not profile_exists:
                raise NmSettingsInvalidConnectionError
            return HOTSPOT

    class Active:
        def __init__(self, path, bus): pass
        @property
        def connection(self):
            async def value(): return active_settings
            return value()

    class Device:
        def __init__(self, path, bus): pass
        async def disconnect(self): calls.disconnected += 1
        @property
        def interface(self):
            async def value(): return 'wlan0'
            return value()

    class ConnectionSettings:
        def __init__(self, path, bus): self.path = path
        async def delete(self): calls.deleted.append(self.path)

    monkeypatch.setattr(hotspot, 'open_system_bus', lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(hotspot, 'NetworkManager', NM)
    monkeypatch.setattr(hotspot, 'NetworkManagerSettings', Settings)
    monkeypatch.setattr(hotspot, 'ActiveConnection', Active)
    monkeypatch.setattr(hotspot, 'NetworkDeviceWireless', Device)
    monkeypatch.setattr(hotspot, 'NetworkConnectionSettings', ConnectionSettings)
    monkeypatch.setattr(hotspot, 'get_default_wireless', lambda: 'wlan0')
    return calls


def test_without_a_hotspot_profile_nothing_is_touched(monkeypatch):
    calls = install(monkeypatch, profile_exists=False, active_settings=HOME)
    asyncio.run(hotspot.stop_hotspot())
    assert calls.disconnected == 0 and calls.deleted == []


def test_a_wifi_network_that_is_not_the_hotspot_stays_connected(monkeypatch):
    calls = install(monkeypatch, profile_exists=True, active_settings=HOME)
    asyncio.run(hotspot.stop_hotspot())
    assert calls.disconnected == 0
    assert calls.deleted == [HOTSPOT]               # the leftover profile still goes


def test_an_active_hotspot_is_disconnected_and_deleted(monkeypatch):
    calls = install(monkeypatch, profile_exists=True, active_settings=HOTSPOT)
    asyncio.run(hotspot.stop_hotspot())
    assert calls.disconnected == 1 and calls.deleted == [HOTSPOT]


def test_is_hotspot_enabled_compares_the_settings_object_of_the_active_connection(monkeypatch):
    install(monkeypatch, profile_exists=True, active_settings=HOME)
    assert asyncio.run(hotspot.is_hotspot_enabled()) is False
    install(monkeypatch, profile_exists=True, active_settings=HOTSPOT)
    assert asyncio.run(hotspot.is_hotspot_enabled()) is True
