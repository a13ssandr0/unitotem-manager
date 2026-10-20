"""
Touch-to-screen mapping: identity keys, the touch screens in /proc/bus/input/devices, the compositor configuration that maps
them, the sysfs identity and the persisted model. No compositor needed.
"""
import pytest

from unitotem_node.models.touch_map import TouchMap
from unitotem_node.system import touch
from unitotem_node.system.touch import TouchDevice


# ── identity ─────────────────────────────────────────────────────────────────

def test_identity_key_has_name_ids_and_serial():
    assert touch.identity_key('Foo Touch', 0x04f3, 0x2a1b, 'SN1') == 'Foo Touch|04f3:2a1b|SN1'


def test_identity_key_without_ids_or_serial():
    assert touch.identity_key(' Foo ', None, None, None) == 'Foo||'


def test_two_identical_panels_are_told_apart_by_port():
    a = TouchDevice(name='Panel', vendor=1, product=2, phys='usb-1/input0')
    b = TouchDevice(name='Panel', vendor=1, product=2, phys='usb-2/input0')
    c = TouchDevice(name='Other', vendor=1, product=3)
    touch.assign_keys([a, b, c])
    assert a.key != b.key
    assert a.key.endswith('@usb-1/input0') and b.key.endswith('@usb-2/input0')
    assert c.key == 'Other|0001:0003|'




# ── the devices of /proc/bus/input/devices ───────────────────────────────────

PROC_DEVICES = """\
I: Bus=0003 Vendor=04f3 Product=2a1b Version=0111
N: Name="ELAN Touchscreen"
P: Phys=usb-0000:00:14.0-2/input0
S: Sysfs=/devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2:1.0/0003:04F3:2A1B.0001/input/input7
U: Uniq=
H: Handlers=mouse1 event5
B: PROP=2
B: EV=b
B: KEY=400 0 0 0 0 0
B: ABS=260800000000003

I: Bus=0003 Vendor=046d Product=c52b Version=0111
N: Name="Logitech USB Receiver Touchpad"
P: Phys=usb-0000:00:14.0-3/input1
S: Sysfs=/devices/x
U: Uniq=
H: Handlers=mouse2 event6
B: PROP=5
B: EV=1b
B: KEY=e520 10000 0 0 0 0
B: ABS=260800000000003

I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
P: Phys=isa0060/serio0/input0
S: Sysfs=/devices/platform/i8042/serio0/input/input0
U: Uniq=
H: Handlers=sleep kbd event0
B: PROP=0
B: EV=120013
B: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe
"""


def test_only_the_touch_screens_are_found():
    devices = touch.parse_devices(PROC_DEVICES)
    assert [d.name for d in devices] == ['ELAN Touchscreen']      # not the touchpad (no DIRECT property), not the keyboard
    d = devices[0]
    assert (d.vendor, d.product, d.phys, d.node) == (0x04f3, 0x2a1b, 'usb-0000:00:14.0-2/input0', '/dev/input/event5')
    assert d.key == 'ELAN Touchscreen|04f3:2a1b|'


def test_no_devices_is_an_empty_list():
    assert touch.parse_devices('') == []
    assert touch.touch_devices('') == []


def test_bitmap_bits_in_both_word_sizes():
    assert touch._bit('260800000000003', 0x35) and touch._bit('260800000000003', 0)
    assert not touch._bit('260800000000003', 2)
    assert touch._bit('1 0000000000000002', 1) and touch._bit('1 0000000000000002', 64)   # two 64-bit words, high one first
    assert not touch._bit('2', 64)


# ── the compositor configuration ─────────────────────────────────────────────

def test_fragment_maps_devices_by_name():
    text = touch.fragment({'ELAN Touchscreen|04f3:2a1b|': 'DP-2', 'Other|0001:0002|SN': 'HDMI-A-1'})
    assert '<touch deviceName="ELAN Touchscreen" mapToOutput="DP-2"' in text
    assert '<touch deviceName="Other" mapToOutput="HDMI-A-1"' in text


def test_fragment_first_mapping_of_a_name_wins_and_is_escaped():
    text = touch.fragment({'Panel|0001:0001|@usb-1': 'DP-1', 'Panel|0001:0001|@usb-2': 'DP-2', 'A"B|0|': 'X'})
    assert text.count('deviceName="Panel"') == 1 and 'mapToOutput="DP-1"' in text
    assert 'deviceName="A&quot;B"' in text


def test_fragment_of_nothing_is_empty():
    assert touch.fragment({}) == ''


def test_apply_mapping_writes_the_nodes_fragment(tmp_path, monkeypatch):
    from utils import labwc
    monkeypatch.setattr(labwc, 'RUN_DIR', tmp_path)
    monkeypatch.setattr(labwc, 'pid', lambda: None)             # no compositor to signal
    assert touch.apply_mapping({'ELAN Touchscreen|04f3:2a1b|': 'DP-2'}) is False
    text = (tmp_path / 'node' / 'labwc' / 'rc.xml').read_text()
    assert text.startswith('<?xml') and 'mapToOutput="DP-2"' in text and text.rstrip().endswith('</labwc_config>')


# ── sysfs identity ───────────────────────────────────────────────────────────

def test_sysfs_identity_walks_up_to_the_usb_serial(tmp_path):
    usb = tmp_path / 'devices' / 'usb1' / '1-2'
    hid = usb / '1-2:1.0' / '0003:04F3:2A1B.0001' / 'input' / 'input7'
    hid.mkdir(parents=True)
    (usb / 'idVendor').write_text('04f3\n')
    (usb / 'serial').write_text('ABC123\n')
    (hid / 'phys').write_text('usb-0000:00:14.0-2/input0\n')
    (hid / 'event5').mkdir()
    cls = tmp_path / 'class' / 'input' / 'event5'
    cls.mkdir(parents=True)
    (cls / 'device').symlink_to(hid)
    found = touch.sysfs_identity('/dev/input/event5', root=str(tmp_path / 'class' / 'input'))
    assert found == {'serial': 'ABC123'}


def test_sysfs_identity_ignores_garbage():
    assert touch.sysfs_identity(None) == {}
    assert touch.sysfs_identity('/etc/passwd') == {}
    assert touch.sysfs_identity('/dev/input/event99999', root='/nonexistent') == {}


# ── persisted mapping ────────────────────────────────────────────────────────

def test_touch_map_set_and_clear():
    m = TouchMap()
    m.set('A|0001:0002|', 'HDMI-1')
    assert m.mapping == {'A|0001:0002|': 'HDMI-1'}
    m.set('A|0001:0002|', None)
    assert m.mapping == {}


def test_touch_map_rejects_bad_output_names():
    with pytest.raises(ValueError):
        TouchMap().set('A', '../etc')


def test_touch_map_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(TouchMap, '_filepath', staticmethod(lambda: tmp_path / 'touch_map.json'))
    m = TouchMap()
    m.set('A|0001:0002|S', 'DP-1')
    m.save()
    assert TouchMap.load().mapping == {'A|0001:0002|S': 'DP-1'}
    assert not (tmp_path / 'touch_map.json.tmp').exists()


def test_touch_map_unreadable_file_gives_defaults(tmp_path, monkeypatch):
    path = tmp_path / 'touch_map.json'
    path.write_text('{not json')
    monkeypatch.setattr(TouchMap, '_filepath', staticmethod(lambda: path))
    assert TouchMap.load().mapping == {}


# ── no compositor ────────────────────────────────────────────────────────────

def test_snapshot_without_a_compositor_is_empty():
    from unitotem_node.system.touch_service import TouchService
    s = TouchService().snapshot()
    assert s['available'] is False and s['outputs'] == []
