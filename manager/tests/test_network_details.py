"""Connection details cross the websocket as JSON: byte arrays (SSID, BSSID) must not break the message."""
import json

from unitotem_node.system.network.networkmanager import _unpack_section


def test_byte_arrays_become_number_lists():
    raw = {'ssid': ('ay', b'FRITZ'), 'bssid': ('ay', bytes([0x96, 0x28, 0xde, 0x02, 0x91, 0xe7])), 'mode': ('s', 'infrastructure'),
           'mac-address-blacklist': ('as', [])}
    out = _unpack_section(raw)
    assert json.dumps(out)                                  # the websocket message can be built
    assert bytes(out['ssid']) == b'FRITZ'
    assert ':'.join(f'{b:02x}' for b in out['bssid']) == '96:28:de:02:91:e7'
    assert out['mode'] == 'infrastructure'


def test_nested_bytes_are_converted_too():
    out = _unpack_section({'address-data': ('aa{sv}', [{'address': ('s', '1.2.3.4'), 'raw': b'\x01'}])})
    assert out['address-data'][0]['raw'] == [1]


def test_variants_at_any_depth_are_unwrapped():
    out = _unpack_section({'address-data': ('aa{sv}', [{'address': ('s', '1.2.3.4'), 'prefix': ('u', 24)}])})
    assert out['address-data'] == [{'address': '1.2.3.4', 'prefix': 24}]


def test_static_dns_is_written_in_memory_order():
    from unitotem_node.system.network.networkmanager import _build_ipv4_section
    section = _build_ipv4_section('manual', '192.168.0.254', 23, '192.168.1.1', ['192.168.1.1', '8.8.4.4'])
    assert [v.to_bytes(4, 'little') for v in section['dns'][1]] == [bytes([192, 168, 1, 1]), bytes([8, 8, 4, 4])]


def test_invalid_ipv4_settings_are_refused():
    import pytest
    from unitotem_node.system.network.networkmanager import InvalidIPSettings, _build_ipv4_section
    bad = [
        ('static', '192.168.0.2', 24, None, None),          # unknown method
        ('manual', None, 24, None, None),                   # static without an address
        ('manual', '192.168.0.300', 24, None, None),        # not an address
        ('manual', '192.168.0', 24, None, None),
        ('manual', 3232235778, 24, None, None),             # an int is not an address string
        ('manual', '192.168.0.2', 0, None, None),           # prefix out of range
        ('manual', '192.168.0.2', 33, None, None),
        ('manual', '192.168.0.2', True, None, None),        # bool is not a prefix
        ('manual', '192.168.0.2', 24, 'gw', None),          # gateway not an address
        ('manual', '192.168.0.2', 24, None, ['8.8.8.8', 'dns.example']),  # invalid DNS is refused, not dropped
    ]
    for args in bad:
        with pytest.raises(InvalidIPSettings):
            _build_ipv4_section(*args)


def test_valid_static_ipv4_settings_are_built():
    from unitotem_node.system.network.networkmanager import _build_ipv4_section
    section = _build_ipv4_section('manual', '10.0.0.5', 24, '10.0.0.1', ['1.1.1.1'])
    assert section['method'] == ('s', 'manual')
    assert section['address-data'][1][0] == {'address': ('s', '10.0.0.5'), 'prefix': ('u', 24)}
    assert section['gateway'] == ('s', '10.0.0.1')
    assert section['dns'][1] == [int.from_bytes(bytes([1, 1, 1, 1]), 'little')]
    assert _build_ipv4_section('auto', None, 24, None, None) == {'method': ('s', 'auto')}
    assert _build_ipv4_section('link-local', None, 24, None, None) == {'method': ('s', 'link-local')}
    assert _build_ipv4_section('disabled', None, 24, None, None) == {'method': ('s', 'disabled')}


def test_api_answers_invalid_ip_settings_with_an_error(monkeypatch):
    import asyncio
    from contextlib import contextmanager
    from unitotem_node.api.network import Network
    from unitotem_node.system.network import networkmanager as nm

    # the D-Bus layer is stubbed: validation must answer before anything reaches it
    @contextmanager
    def bus():
        yield None

    class Conn:
        def __init__(self, path, bus): pass
        async def get_settings(self): return {}
        async def update(self, settings): raise AssertionError('settings were written with invalid input')

    monkeypatch.setattr(nm, 'open_system_bus', bus)
    monkeypatch.setattr(nm, 'NetworkConnectionSettings', Conn)

    async def first(gen):
        return [item async for item in gen][0]

    r = asyncio.run(first(Network.connect_wifi('net', 'wlan0', ip_method='bogus')))
    assert r.kwargs['error'] == 'Invalid IP settings' and 'bogus' in r.kwargs['extra']
    r = asyncio.run(Network.edit_connection('/conn/1', ip_method='manual', ip_address='10.0.0.300', prefix_len=24))
    assert r.kwargs['error'] == 'Invalid IP settings'
