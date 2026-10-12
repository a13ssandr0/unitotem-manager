"""Pure parts of the screen control hardware layer: xrandr, CEC, Wake-on-LAN, serial, the model, mechanism order."""
import pytest

from unitotem_node.models.screen_control import OutputConfig, SerialConfig, WolConfig
from unitotem_node.system.screens import cec, serial, wol, xorg
from unitotem_node.system.screens.ddc_ctl import power_off_value
from unitotem_node.system.screens.mechanism import NoMechanismAvailable, candidates

XRANDR = """Screen 0: minimum 320 x 200, current 5760 x 2160, maximum 16384 x 16384
DP-1 connected primary 3840x2160+0+0 (normal left inverted right x axis y axis) 600mm x 340mm
   3840x2160     60.00*+  30.00
HDMI-A-1 connected 1920x1080+3840+0 left (normal left inverted right x axis y axis) 510mm x 290mm
   1920x1080     60.00*+
HDMI-A-2 connected (normal left inverted right x axis y axis)
   1920x1080     60.00 +
DP-2 disconnected (normal left inverted right x axis y axis)
"""


def test_parse_xrandr_query_states_and_geometry():
    outs = {o.name: o for o in xorg.parse_xrandr_query(XRANDR)}
    assert outs['DP-1'].active and (outs['DP-1'].width, outs['DP-1'].height) == (3840, 2160)
    assert outs['HDMI-A-1'].x == 3840 and outs['HDMI-A-1'].rotation == 'left'
    assert outs['HDMI-A-2'].connected and not outs['HDMI-A-2'].active   # connected but switched off
    assert not outs['DP-2'].connected


def test_cec_device_list_and_power_status():
    text = 'vc4_hdmi (platform:fef00700.hdmi):\n\t/dev/cec0\n\nvivid (platform:vivid-000):\n\t/dev/cec1\n'
    assert [d.path for d in cec.parse_list_devices(text)] == ['/dev/cec0', '/dev/cec1']
    assert cec.parse_list_devices('') == []
    assert cec.parse_power_status('\tpwr-state: on (0x00)') == 'on'
    assert cec.parse_power_status('\tpwr-state: to-standby (0x03)') == 'standby'
    assert cec.parse_power_status('Timeout') is None


def test_magic_packet_layout():
    packet = wol.magic_packet('AA-BB-CC-DD-EE-FF')
    assert len(packet) == 102 and packet[:6] == b'\xff' * 6
    assert packet[6:12] == bytes.fromhex('aabbccddeeff') and packet[96:] == bytes.fromhex('aabbccddeeff')
    assert wol.normalise_mac('aabbccddeeff') == 'aa:bb:cc:dd:ee:ff'
    with pytest.raises(ValueError):
        wol.normalise_mac('not a mac')


def test_ethernet_frame_is_what_routeros_sends():
    src = bytes.fromhex('1c697aabcac3')
    frame = wol.ethernet_frame('ec:c1:ab:28:bf:c1', src)
    assert frame[:6] == b'\xff' * 6 and frame[6:12] == src and frame[12:14] == b'\x08\x42'
    assert frame[14:] == wol.magic_packet('ec:c1:ab:28:bf:c1')


def test_wol_config_defaults_and_validation():
    cfg = WolConfig(mac='ec:c1:ab:28:bf:c1')
    assert cfg.repeat == 10 and cfg.interface is None
    with pytest.raises(ValueError):
        WolConfig(mac='ec:c1:ab:28:bf:c1', interface='eth0; reboot')


def test_serial_command_escapes_and_port_validation():
    assert serial.parse_command('ka 00 01\\r') == b'ka 00 01\r'
    assert serial.parse_command('\\xAA\\x11\\xFE') == b'\xaa\x11\xfe'
    assert serial.valid_port('/dev/ttyUSB0') and serial.valid_port('/dev/ttyS1')
    assert not serial.valid_port('/etc/shadow') and not serial.valid_port('/dev/sda')
    with pytest.raises(UnicodeEncodeError):
        serial.parse_command('é')


def test_models_validate_what_reaches_the_system():
    assert WolConfig(mac='AA-BB-CC-DD-EE-FF').mac == 'aa:bb:cc:dd:ee:ff'
    with pytest.raises(ValueError):
        WolConfig(mac='zz')
    with pytest.raises(ValueError):
        SerialConfig(port='/dev/sda')
    cfg = OutputConfig(serial=SerialConfig(port='/dev/ttyUSB0', on_command='PON\\r', off_command='POF\\r'))
    assert cfg.power_on_at_start is True


def test_auto_order_with_the_new_mechanisms():
    assert candidates({'x', 'ddc', 'wol'}, 'on') == ['wol', 'ddc', 'x']
    assert candidates({'x', 'ddc', 'serial', 'cec'}, 'off') == ['ddc', 'cec', 'serial', 'x']
    # WOL has no off
    with pytest.raises(NoMechanismAvailable):
        candidates({'wol'}, 'off')
    assert candidates({'x', 'ddc'}, 'on', 'ddc') == ['ddc']
    with pytest.raises(NoMechanismAvailable):
        candidates({'x'}, 'on', 'ddc')


def test_power_off_value_follows_what_the_monitor_advertises():
    assert power_off_value({'D6': {'01': 'On', '04': 'Standby'}}) == 0x04
    assert power_off_value({'D6': {'01': 'On', '05': 'Off'}}) == 0x05
    assert power_off_value({}) == 0x04
