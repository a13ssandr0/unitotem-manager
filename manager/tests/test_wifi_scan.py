"""Wi-Fi access point list and scan (system/network/wifi.py), the wireless_networks/connect_wifi websocket calls
(api/network.py) and the SSID/passphrase checks of a client connection (system/network/networkmanager.py).
NetworkManager is replaced by a fake: no D-Bus, no radio, no sleep."""
import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from api.ws.responses import WSBroadcast, WSResponse
from unitotem_node.api import network as network_api
from unitotem_node.system.network import networkmanager, wifi

WIFI, ETHERNET = 2, 1          # NM device types, as sdbus_async.networkmanager.DeviceType holds them


class Aw:
    """An awaitable NM property."""
    def __init__(self, value):
        self.value = value

    def __await__(self):
        return self._get().__await__()

    async def _get(self):
        return self.value


class State:
    def __init__(self):
        self.hardware_enabled = True
        self.enabled = True
        self.devices = {}               # object path -> (device type, interface, [access point paths])
        self.aps = {}                   # access point path -> properties dict
        self.scans = []                 # device paths asked to scan
        self.set_enabled = []           # values written to wireless_enabled
        self.added = []                 # connection settings passed to add_connection
        self.updated = []
        self.activated = []             # (connection path, device path)
        self.existing = None            # uuid -> path of an existing profile (connect_wifi reuse)


def install(monkeypatch, state):
    class EnabledProp(Aw):
        async def set_async(self, value):
            state.set_enabled.append(value)
            state.enabled = value

    class NM:
        def __init__(self, bus): pass

        @property
        def wireless_hardware_enabled(self): return Aw(state.hardware_enabled)

        @property
        def wireless_enabled(self): return EnabledProp(state.enabled)

        async def get_devices(self): return list(state.devices)

        async def get_device_by_ip_iface(self, iface): return f'/dev/{iface}'

        async def activate_connection(self, conn, dev, *extra):
            state.activated.append((conn, dev))
            return '/active/1'

    class Device:
        def __init__(self, path, bus): self.path = path

        @property
        def device_type(self): return Aw(state.devices[self.path][0])

        @property
        def interface(self): return Aw(state.devices[self.path][1])

        async def get_all_access_points(self): return list(state.devices[self.path][2])

        async def request_scan(self, options): state.scans.append(self.path)

    class AccessPoint:
        def __init__(self, path, bus): self.path = path

        async def properties_get_all_dict(self, *args): return dict(state.aps[self.path])

    class Settings:
        def __init__(self, bus): pass

        async def get_connection_by_uuid(self, uuid):
            if state.existing and uuid in state.existing:
                return state.existing[uuid]
            raise networkmanager.NmSettingsInvalidConnectionError

        async def add_connection(self, settings):
            state.added.append(settings)
            return '/settings/new'

    class ConnectionSettings:
        def __init__(self, path, bus): self.path = path

        async def update(self, settings): state.updated.append((self.path, settings))

    async def no_sleep(seconds): pass

    nullbus = lambda: contextlib.nullcontext(object())
    for module in (wifi, networkmanager):
        monkeypatch.setattr(module, 'open_system_bus', nullbus)
        monkeypatch.setattr(module, 'NetworkManager', NM)
    monkeypatch.setattr(wifi, 'NetworkDeviceWireless', Device)
    monkeypatch.setattr(wifi, 'AccessPoint', AccessPoint)
    monkeypatch.setattr(wifi, 'sleep', no_sleep)
    monkeypatch.setattr(networkmanager, 'NetworkManagerSettings', Settings)
    monkeypatch.setattr(networkmanager, 'NetworkConnectionSettings', ConnectionSettings)
    return state


@pytest.fixture
def state(monkeypatch):
    return install(monkeypatch, State())


def run(coro):
    return asyncio.run(coro)


# ── scan and list ─────────────────────────────────────────────────────────

def test_access_points_are_sorted_strongest_first_with_text_ssids(state):
    state.devices['/dev/wlan0'] = (WIFI, 'wlan0', ['/ap/1', '/ap/2', '/ap/3'])
    state.aps = {'/ap/1': {'ssid': b'Weak', 'strength': 30},
                 '/ap/2': {'ssid': b'Strong', 'strength': 90},
                 '/ap/3': {'ssid': b'Mid', 'strength': 60}}
    aps = run(wifi.get_access_points())
    assert [ap['ssid'] for ap in aps] == ['Strong', 'Mid', 'Weak']
    assert all(isinstance(ap['ssid'], str) for ap in aps)


def test_an_access_point_seen_by_two_devices_is_listed_once(state):
    state.devices['/dev/wlan0'] = (WIFI, 'wlan0', ['/ap/1'])
    state.devices['/dev/wlan1'] = (WIFI, 'wlan1', ['/ap/1'])
    state.aps = {'/ap/1': {'ssid': b'Home', 'strength': 70}}
    assert [ap['ssid'] for ap in run(wifi.get_access_points())] == ['Home']


def test_wired_devices_contribute_no_access_points(state):
    state.devices['/dev/eth0'] = (ETHERNET, 'eth0', ['/ap/x'])
    state.aps = {'/ap/x': {'ssid': b'Ghost', 'strength': 99}}
    assert run(wifi.get_access_points()) == []


def test_a_hidden_network_has_an_empty_ssid(state):
    state.devices['/dev/wlan0'] = (WIFI, 'wlan0', ['/ap/h'])
    state.aps = {'/ap/h': {'ssid': b'', 'strength': 40}}
    assert run(wifi.get_access_points()) == [{'ssid': '', 'strength': 40}]


def test_a_scan_asks_every_wifi_device_and_only_those(state):
    state.devices = {'/dev/wlan0': (WIFI, 'wlan0', []), '/dev/eth0': (ETHERNET, 'eth0', []),
                     '/dev/wlan1': (WIFI, 'wlan1', [])}
    run(wifi.scan_access_points())
    assert sorted(state.scans) == ['/dev/wlan0', '/dev/wlan1']


def test_a_scan_with_the_radio_off_turns_it_on_first(state):
    state.enabled = False
    state.devices = {'/dev/wlan0': (WIFI, 'wlan0', [])}
    run(wifi.scan_access_points())
    assert state.set_enabled == [True] and state.scans == ['/dev/wlan0']


def test_a_scan_without_wireless_hardware_is_an_error_and_scans_nothing(state):
    state.hardware_enabled = False
    state.devices = {'/dev/wlan0': (WIFI, 'wlan0', [])}
    with pytest.raises(RuntimeError):
        run(wifi.scan_access_points())
    assert state.scans == []


def test_the_list_call_refuses_without_wireless_hardware(state):
    state.hardware_enabled = False
    with pytest.raises(RuntimeError):
        run(wifi.get_access_points())


def test_a_non_utf8_ssid_does_not_break_the_whole_list(state):
    """SSIDs are arbitrary bytes (802.11). One such network must not make the list fail for every page."""
    state.devices['/dev/wlan0'] = (WIFI, 'wlan0', ['/ap/bad', '/ap/ok'])
    state.aps = {'/ap/bad': {'ssid': b'\xff\xfe\x80', 'strength': 80},
                 '/ap/ok': {'ssid': b'Office', 'strength': 50}}
    aps = run(wifi.get_access_points())
    assert len(aps) == 2
    assert 'Office' in [ap['ssid'] for ap in aps]


def test_wireless_networks_scans_then_replies_with_the_list(state):
    state.devices['/dev/wlan0'] = (WIFI, 'wlan0', ['/ap/1'])
    state.aps = {'/ap/1': {'ssid': b'Net', 'strength': 55}}
    reply = run(network_api.Network.wireless_networks())
    assert state.scans == ['/dev/wlan0']
    assert isinstance(reply, WSBroadcast)
    assert reply.kwargs['wifis'] == [{'ssid': 'Net', 'strength': 55}]


# ── SSID and passphrase of a client connection ───────────────────────────
# IEEE 802.11: an SSID is 1 to 32 bytes; a WPA2 passphrase is 8 to 63 ASCII characters (64 hex digits are a raw key).

def connect(ssid, password=None, **kwargs):
    return networkmanager.connect_wifi(ssid, password, 'wlan0', **kwargs)


def test_a_normal_wpa_network_is_added_and_activated(state):
    uid = run(connect('Home', 'correct-horse'))
    assert uid and len(state.added) == 1
    wireless = state.added[0]['802-11-wireless']
    assert wireless['ssid'] == ('ay', b'Home')
    assert state.added[0]['802-11-wireless-security']['psk'] == ('s', 'correct-horse')
    assert state.activated == [('/settings/new', '/dev/wlan0')]


def test_an_open_network_has_no_security_section(state):
    run(connect('Guest'))
    assert '802-11-wireless-security' not in state.added[0]


@pytest.mark.parametrize('ssid', ['', 'x' * 33, 'è' * 17])      # 'è' is two bytes: 34 bytes in total
def test_an_ssid_outside_1_to_32_bytes_is_refused_before_anything_is_written(state, ssid):
    with pytest.raises(ValueError):
        run(connect(ssid, 'correct-horse'))
    assert state.added == [] and state.activated == []


@pytest.mark.parametrize('ssid', ['x' * 32, 'è' * 16])          # both exactly 32 bytes
def test_an_ssid_of_32_bytes_is_accepted(state, ssid):
    run(connect(ssid, 'correct-horse'))
    assert state.added[0]['802-11-wireless']['ssid'] == ('ay', ssid.encode())


@pytest.mark.parametrize('password', ['1234567', 'x' * 64, 'x' * 100])
def test_a_passphrase_outside_8_to_63_characters_is_refused(state, password):
    with pytest.raises(ValueError):
        run(connect('Home', password))
    assert state.added == [] and state.activated == []


@pytest.mark.parametrize('password', ['12345678', 'x' * 63])
def test_a_passphrase_of_8_to_63_characters_is_accepted(state, password):
    run(connect('Home', password))
    assert state.added[0]['802-11-wireless-security']['psk'] == ('s', password)


def test_a_raw_64_digit_key_is_accepted(state):
    run(connect('Home', 'ab' * 32))
    assert state.added[0]['802-11-wireless-security']['psk'] == ('s', 'ab' * 32)


def test_a_bssid_must_be_six_hex_octets(state):
    with pytest.raises(ValueError):
        run(connect('Home', 'correct-horse', bssid='zz:01:02'))
    assert state.added == []


@pytest.fixture
def api_bus(monkeypatch):
    """A connection that does succeed would reach the hotspot check and the device lists: keep them off D-Bus."""
    async def no_hotspot(): return False
    async def empty(): return []
    monkeypatch.setattr(network_api, 'is_hotspot_enabled', no_hotspot)
    monkeypatch.setattr(networkmanager, 'get_devices', empty)
    monkeypatch.setattr(networkmanager, 'get_active_connections', empty)


def test_the_api_reports_an_invalid_ip_setting_as_an_error_and_writes_nothing(state, api_bus):
    async def collect():
        return [m async for m in network_api.Network.connect_wifi('Home', 'wlan0', 'correct-horse',
                                                                  ip_method='manual', ip_address='not-an-ip')]
    replies = run(collect())
    assert len(replies) == 1 and isinstance(replies[0], WSResponse)
    assert replies[0].kwargs['error'] == 'Invalid IP settings'
    assert state.added == [] and state.activated == []


def test_the_api_reports_an_invalid_ssid_as_an_error_and_writes_nothing(state, api_bus):
    async def collect():
        return [m async for m in network_api.Network.connect_wifi('x' * 33, 'wlan0', 'correct-horse')]
    replies = run(collect())
    assert state.added == [] and state.activated == []
    assert any(isinstance(r, WSResponse) and r.kwargs.get('error') for r in replies)
