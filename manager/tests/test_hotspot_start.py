"""start_hotspot: the fallback access point's NetworkManager profile (parameters, reuse, recreation, defaults, errors) and
its SSID/passphrase checks. NetworkManager is replaced by a fake: no D-Bus, no radio, no sleep."""
import asyncio
import contextlib
import os
from types import SimpleNamespace

import pytest
from sdbus_async.networkmanager import NmSettingsInvalidConnectionError
from sdbus_async.networkmanager.exceptions import NetworkManagerPermissionDeniedError

from unitotem_node.system.network import hotspot

UUID = '3f1c2b7e-0a5d-4c1e-9b8a-6d2f4e7a1b00'
PATH = '/settings/hotspot'
MAC = 'D8:3A:DD:12:34:5F'
DEFAULT_PASSWORD = 'DD12345F'           # last 8 hex digits of the MAC, upper case


class Aw:
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
        self.device = True                  # a wifi device exists
        self.denied = False                 # NetworkManager refuses every call
        self.stored = None                  # (ssid bytes, psk) of the existing hotspot profile, or None
        self.secrets_broken = False         # the stored profile cannot be read
        self.added = []
        self.deleted = []
        self.activated = []
        self.set_enabled = []


def install(monkeypatch, state, hostname='unitotem-test'):
    class EnabledProp(Aw):
        async def set_async(self, value):
            state.set_enabled.append(value)
            state.enabled = value

    def check():
        if state.denied:
            raise NetworkManagerPermissionDeniedError()

    class NM:
        def __init__(self, bus): check()

        @property
        def wireless_hardware_enabled(self): return Aw(state.hardware_enabled)

        @property
        def wireless_enabled(self): return EnabledProp(state.enabled)

        async def get_device_by_ip_iface(self, iface): return '/dev/wlan0'

        async def activate_connection(self, conn, dev, *extra):
            state.activated.append((conn, dev))

    class Settings:
        def __init__(self, bus): pass

        async def get_connection_by_uuid(self, uuid):
            if uuid != UUID or state.stored is None:
                raise NmSettingsInvalidConnectionError
            return PATH

        async def add_connection(self, settings):
            state.added.append(settings)
            return '/settings/new'

    class Conn:
        def __init__(self, path, bus): self.path = path

        async def get_settings(self):
            ssid, _ = state.stored
            return {'802-11-wireless': {'ssid': ('ay', ssid)}}

        async def get_secrets(self, name):
            if state.secrets_broken:
                raise RuntimeError('no secrets')
            return {name: {'psk': ('s', state.stored[1])}}

        async def delete(self):
            state.deleted.append(self.path)
            state.stored = None

    class Device:
        def __init__(self, path, bus): pass

        @property
        def interface(self): return Aw('wlan0')

        @property
        def hw_address(self): return Aw(MAC)

    async def no_sleep(seconds): pass

    monkeypatch.setitem(os.environ, 'hotspot_uuid', UUID)
    monkeypatch.setattr(hotspot, 'open_system_bus', lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(hotspot, 'NetworkManager', NM)
    monkeypatch.setattr(hotspot, 'NetworkManagerSettings', Settings)
    monkeypatch.setattr(hotspot, 'NetworkConnectionSettings', Conn)
    monkeypatch.setattr(hotspot, 'NetworkDeviceWireless', Device)
    monkeypatch.setattr(hotspot, 'get_default_wireless', lambda: 'wlan0' if state.device else None)
    monkeypatch.setattr(hotspot, 'gethostname', lambda: hostname)
    monkeypatch.setattr(hotspot, 'sleep', no_sleep)
    return state


@pytest.fixture
def state(monkeypatch):
    return install(monkeypatch, State())


def start(ssid=None, password=None):
    return asyncio.run(hotspot.start_hotspot(ssid, password))


# ── new profile ───────────────────────────────────────────────────────────

def test_without_arguments_the_hostname_and_the_mac_derived_password_are_used(state):
    assert start() == ('unitotem-test', DEFAULT_PASSWORD)
    settings = state.added[0]
    assert settings['802-11-wireless']['ssid'] == ('ay', b'unitotem-test')
    assert settings['802-11-wireless-security']['psk'] == ('s', DEFAULT_PASSWORD)


def test_the_profile_is_a_shared_access_point_on_channel_1_with_the_fixed_uuid(state):
    start()
    s = state.added[0]
    assert s['connection']['uuid'] == ('s', UUID)
    assert s['connection']['type'] == ('s', '802-11-wireless')
    assert s['connection']['id'] == ('s', 'UniTotem first boot hotspot')
    assert s['802-11-wireless']['mode'] == ('s', 'ap')
    assert s['802-11-wireless']['band'] == ('s', 'bg')
    assert s['802-11-wireless']['channel'] == ('u', 1)
    assert s['802-11-wireless-security']['key-mgmt'] == ('s', 'wpa-psk')
    assert s['ipv4']['method'] == ('s', 'shared')
    assert s['ipv6']['method'] == ('s', 'ignore')


def test_the_new_profile_is_activated_on_the_wifi_device(state):
    start('Setup', 'correct-horse')
    assert state.activated == [('/settings/new', '/dev/wlan0')]


def test_the_requested_ssid_and_password_are_used(state):
    assert start('Totem Setup', '12345678') == ('Totem Setup', '12345678')
    assert state.added[0]['802-11-wireless']['ssid'] == ('ay', 'Totem Setup'.encode())


def test_only_the_password_given_keeps_the_default_ssid(state):
    assert start(None, 'correct-horse') == ('unitotem-test', 'correct-horse')


def test_only_the_ssid_given_keeps_the_default_password(state):
    assert start('Setup', None) == ('Setup', DEFAULT_PASSWORD)


# ── existing profile ──────────────────────────────────────────────────────

def test_a_matching_profile_is_reused_as_it_is(state):
    state.stored = (b'Setup', 'correct-horse')
    assert start('Setup', 'correct-horse') == ('Setup', 'correct-horse')
    assert state.added == [] and state.deleted == []
    assert state.activated == [(PATH, '/dev/wlan0')]


def test_without_arguments_the_stored_values_are_used(state):
    state.stored = (b'Kiosk', 'stored-pass')
    assert start() == ('Kiosk', 'stored-pass')
    assert state.added == [] and state.deleted == []
    assert state.activated == [(PATH, '/dev/wlan0')]


def test_a_different_ssid_or_password_recreates_the_profile(state):
    state.stored = (b'Old', 'old-pass')
    assert start('New', 'new-pass') == ('New', 'new-pass')
    assert state.deleted == [PATH]
    assert state.added[0]['802-11-wireless']['ssid'] == ('ay', b'New')
    assert state.activated == [('/settings/new', '/dev/wlan0')]


def test_an_unreadable_profile_is_deleted_and_recreated_with_the_defaults(state):
    state.stored = (b'Old', 'old-pass')
    state.secrets_broken = True
    assert start() == ('unitotem-test', DEFAULT_PASSWORD)
    assert state.deleted == [PATH] and len(state.added) == 1


# ── errors ────────────────────────────────────────────────────────────────

def test_no_wifi_device_is_an_error_and_nothing_is_written(state):
    state.device = False
    with pytest.raises(RuntimeError):
        start()
    assert state.added == [] and state.activated == []


def test_wireless_hardware_switched_off_is_an_error(state):
    state.hardware_enabled = False
    with pytest.raises(RuntimeError):
        start()
    assert state.added == [] and state.activated == []


def test_wireless_is_switched_on_before_starting(state):
    state.enabled = False
    assert start('Setup', 'correct-horse') == ('Setup', 'correct-horse')
    assert state.set_enabled == [True]


def test_wireless_that_stays_off_is_an_error(state, monkeypatch):
    class Stuck(Aw):
        async def set_async(self, value): state.set_enabled.append(value)

    monkeypatch.setattr(hotspot.NetworkManager, 'wireless_enabled', property(lambda self: Stuck(False)))
    with pytest.raises(RuntimeError):
        start('Setup', 'correct-horse')
    assert state.set_enabled == [True]
    assert state.added == [] and state.activated == []


def test_permission_denied_returns_empty_values_and_writes_nothing(state):
    state.denied = True
    assert start() == ('', '')
    assert state.added == [] and state.activated == []


# ── SSID and passphrase ───────────────────────────────────────────────────
# IEEE 802.11: an SSID is 1 to 32 bytes; a WPA2 passphrase is 8 to 63 ASCII characters (64 hex digits are a raw key).

@pytest.mark.parametrize('ssid', ['', 'x' * 33, 'è' * 17])          # 'è' is two bytes
def test_an_ssid_outside_1_to_32_bytes_is_refused(state, ssid):
    with pytest.raises(ValueError):
        start(ssid, 'correct-horse')
    assert state.added == [] and state.activated == []


def test_an_ssid_of_32_bytes_is_accepted(state):
    assert start('x' * 32, 'correct-horse')[0] == 'x' * 32


@pytest.mark.parametrize('password', ['', '1234567', 'x' * 64])
def test_a_passphrase_outside_8_to_63_characters_is_refused(state, password):
    with pytest.raises(ValueError):
        start('Setup', password)
    assert state.added == [] and state.activated == []


@pytest.mark.parametrize('password', ['12345678', 'x' * 63])
def test_a_passphrase_of_8_to_63_characters_is_accepted(state, password):
    assert start('Setup', password) == ('Setup', password)


def test_a_default_ssid_longer_than_32_bytes_is_refused_or_shortened(state, monkeypatch):
    """Without an SSID argument the hostname is the SSID; a hostname can be longer than an SSID allows."""
    install(monkeypatch, state, hostname='h' * 40)
    try:
        ssid, _ = start()
    except ValueError:
        assert state.added == []
        return
    assert len(ssid.encode()) <= 32
