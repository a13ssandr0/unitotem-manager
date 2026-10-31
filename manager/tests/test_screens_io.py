"""Pure parts of the screen control hardware layer: the compositor's outputs, CEC, Wake-on-LAN, serial, the model, mechanism order."""
import json

import pytest

from unitotem_node.models.screen_control import OutputConfig, SerialConfig, WolConfig
from unitotem_node.system.screens import cec, serial, wayland, wol
from unitotem_node.system.screens.ddc_ctl import power_off_value
from unitotem_node.system.screens.mechanism import NoMechanismAvailable, candidates

OUTPUTS = json.dumps([
    {'name': 'DP-1', 'make': 'GSM', 'model': 'LG ULTRAFINE', 'serial': '401', 'enabled': True,
     'modes': [{'width': 3840, 'height': 2160, 'refresh': 60.0, 'preferred': True, 'current': True},
               {'width': 1920, 'height': 1080, 'refresh': 60.0, 'preferred': False, 'current': False}],
     'position': {'x': 0, 'y': 0}, 'transform': 'normal', 'scale': 1.0},
    {'name': 'HDMI-A-1', 'make': '', 'model': '', 'serial': '', 'enabled': True,
     'modes': [{'width': 1920, 'height': 1080, 'refresh': 60.0, 'preferred': True, 'current': True}],
     'position': {'x': 3840, 'y': 0}, 'transform': '90', 'scale': 1.0},
    {'name': 'HDMI-A-2', 'enabled': False,
     'modes': [{'width': 1920, 'height': 1080, 'refresh': 60.0, 'preferred': True, 'current': False}],
     'position': {'x': 0, 'y': 0}, 'transform': 'normal', 'scale': 1.0},
])


def test_parse_outputs_states_and_geometry():
    outs = {o.name: o for o in wayland.parse_outputs(OUTPUTS)}
    assert outs['DP-1'].active and (outs['DP-1'].width, outs['DP-1'].height) == (3840, 2160)
    # a rotated output covers the rotated rectangle in the layout, and the web UI keeps xrandr's names for rotations
    assert outs['HDMI-A-1'].x == 3840 and outs['HDMI-A-1'].rotation == 'left'
    assert (outs['HDMI-A-1'].width, outs['HDMI-A-1'].height) == (1080, 1920)
    assert outs['HDMI-A-2'].connected and not outs['HDMI-A-2'].active       # connected but switched off


def test_parse_outputs_of_garbage_is_empty():
    assert wayland.parse_outputs('not json') == [] and wayland.parse_outputs('{}') == []


def test_edid_is_read_from_the_connector_of_the_same_name(tmp_path):
    (tmp_path / 'card0-DP-2').mkdir()
    (tmp_path / 'card0-DP-2' / 'edid').write_bytes(b'\x00\xff')
    (tmp_path / 'card0-DP-3').mkdir()
    assert wayland.read_edid('DP-2', str(tmp_path)) == b'\x00\xff'
    assert wayland.read_edid('DP-3', str(tmp_path)) is None
    assert wayland.read_edid('HDMI-A-1', str(tmp_path)) is None


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
    assert candidates({'output', 'ddc', 'wol'}, 'on') == ['wol', 'output', 'ddc']
    assert candidates({'output', 'ddc', 'serial', 'cec'}, 'off') == ['output', 'ddc', 'cec', 'serial']
    # a screen WE switched off over DDC is woken through DDC first: the compositor would 'succeed' and wake nothing
    assert candidates({'output', 'ddc', 'cec'}, 'on', commanded_off='ddc') == ['ddc', 'output', 'cec']
    assert candidates({'output', 'ddc', 'cec'}, 'off', commanded_off='ddc') == ['output', 'ddc', 'cec']
    # WOL has no off
    with pytest.raises(NoMechanismAvailable):
        candidates({'wol'}, 'off')
    assert candidates({'output', 'ddc'}, 'on', 'ddc') == ['ddc']
    with pytest.raises(NoMechanismAvailable):
        candidates({'output'}, 'on', 'ddc')


def test_power_off_value_follows_what_the_monitor_advertises():
    assert power_off_value({'D6': {'01': 'On', '04': 'Standby'}}) == 0x04
    assert power_off_value({'D6': {'01': 'On', '05': 'Off'}}) == 0x05
    assert power_off_value({}) == 0x04


def test_content_transform_table_covers_every_turn_and_flip():
    table = wayland.CONTENT_TO_TRANSFORM
    assert set(table) == {(r, f) for r in range(4) for f in range(3)}
    assert wayland.TRANSFORMS == {'normal', '90', '180', '270', 'flipped', 'flipped-90', 'flipped-180', 'flipped-270'}
    # a mirror turned half a turn is the mirror along the other axis
    assert table[(0, 2)] == table[(2, 1)] and table[(0, 1)] == table[(2, 2)]
    # content turned a quarter clockwise is the output turned three quarters, and bad input counts as no mirror
    assert wayland.transform_for(1, 0) == '270' and wayland.transform_for(3, 0) == '90'
    assert wayland.transform_for(5, 7) == wayland.transform_for(1, 0)


def test_parse_outputs_keeps_the_mirror_in_the_transform():
    text = json.dumps([{'name': 'DP-1', 'enabled': True, 'modes': [{'width': 1920, 'height': 1080, 'current': True}],
                        'position': {'x': 0, 'y': 0}, 'transform': 'flipped-90'}])
    out = wayland.parse_outputs(text)[0]
    assert out.transform == 'flipped-90' and out.rotation == 'left' and (out.width, out.height) == (1080, 1920)


def test_set_transform_refuses_unknown_names_and_reports_the_compositor(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    calls = []

    async def fake_run(cmd, timeout=None):
        calls.append(cmd)
        return SimpleNamespace(returncode=1 if cmd[-1] == '180' else 0, stdout='', stderr='refused')
    monkeypatch.setattr(wayland, 'run', fake_run)
    with pytest.raises(ValueError):
        asyncio.run(wayland.set_transform('DP-1', 'sideways'))
    asyncio.run(wayland.set_transform('DP-1', '270'))
    assert calls == [['wlr-randr', '--output', 'DP-1', '--transform', '270']]
    with pytest.raises(RuntimeError, match='refused'):
        asyncio.run(wayland.set_transform('DP-1', '180'))


def test_boot_orientation_writes_only_what_differs_and_survives_a_missing_helper(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from unitotem_node.system import boot_orientation as bo

    calls = []

    async def fake_run(cmd, timeout=None):
        calls.append(cmd[1:])
        if cmd[1] == 'get':
            return SimpleNamespace(returncode=0, stdout=json.dumps({'connector': cmd[2], 'orientation': state['now']}), stderr='')
        return SimpleNamespace(returncode=state['rc'], stdout='{"error": "x"}', stderr='')
    state = {'now': 'normal', 'rc': 0}
    monkeypatch.setattr(bo, 'run', fake_run)
    monkeypatch.setattr(bo.shutil, 'which', lambda name: None)
    assert asyncio.run(bo.set_turn('HDMI-A-1', 1)) is False and calls == []          # no helper on this image
    monkeypatch.setattr(bo.shutil, 'which', lambda name: '/usr/sbin/' + name)
    assert asyncio.run(bo.set_turn('HDMI-A-1', 1)) is True
    assert calls == [['get', 'HDMI-A-1'], ['set', 'HDMI-A-1', 'right']]
    state['now'] = 'right'
    calls.clear()
    assert asyncio.run(bo.set_turn('HDMI-A-1', 5)) is False and calls == [['get', 'HDMI-A-1']]      # already so: no write
    state.update(now='normal', rc=1)
    assert asyncio.run(bo.set_turn('HDMI-A-1', 3)) is False                                          # a failure is only logged


def test_blanked_outputs_do_not_count_as_active_but_stay_enabled():
    out = wayland.parse_outputs(OUTPUTS, frozenset({'DP-1'}))
    by = {o.name: o for o in out}
    assert not by['DP-1'].active and by['DP-1'].enabled and by['HDMI-A-1'].active


def test_power_off_blanks_without_disabling_the_output_and_falls_back(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    calls = []

    async def fake_run(cmd, timeout=None):
        calls.append(cmd[0] + ' ' + ' '.join(cmd[1:]))
        return SimpleNamespace(returncode=1 if (cmd[0] == 'wlopm' and fail['wlopm']) else 0, stdout='', stderr='no protocol')
    fail = {'wlopm': False}
    monkeypatch.setattr(wayland, 'run', fake_run)
    asyncio.run(wayland.set_power('DP-1', False))
    assert calls == ['wlopm --off DP-1']                  # the window stays: nothing was disabled
    calls.clear()
    fail['wlopm'] = True
    asyncio.run(wayland.set_power('DP-1', False))
    assert calls == ['wlopm --off DP-1', 'wlr-randr --output DP-1 --off']


def test_an_output_we_blanked_is_blanked_again_when_something_switches_it_on(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    calls, state = [], {'blanked': set()}

    async def fake_run(cmd, timeout=None):
        calls.append(' '.join(cmd))
        if cmd[0] == 'wlopm' and len(cmd) == 3:
            (state['blanked'].add if cmd[1] == '--off' else state['blanked'].discard)(cmd[2])
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if cmd[0] == 'wlopm':
            return SimpleNamespace(returncode=0, stdout=''.join(f'{n} {"off" if n in state["blanked"] else "on"}\n' for n in ('DP-1', 'DP-2')), stderr='')
        return SimpleNamespace(returncode=0, stdout=OUTPUTS, stderr='')
    monkeypatch.setattr(wayland, 'run', fake_run)
    monkeypatch.setattr(wayland, '_watch_uevents', lambda: None)
    wayland._kept_off.clear()

    async def scenario():
        await wayland.set_power('DP-1', False)
        assert wayland._kept_off == {'DP-1'}
        state['blanked'].clear()                       # the compositor switched it on by itself
        calls.clear()
        await wayland.keep_off()
        assert 'wlopm --off DP-1' in calls
        await wayland.set_power('DP-1', True)           # the operator wants it on: no longer kept off
        assert wayland._kept_off == set()
        calls.clear()
        await wayland.keep_off()
        assert not any('--off' in c for c in calls)
    asyncio.run(scenario())
