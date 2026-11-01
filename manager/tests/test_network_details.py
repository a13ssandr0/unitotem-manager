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
