"""
Touch-to-screen mapping: identity keys, the transformation matrix for an output (position, size, rotation), the plan
of what to write, the XI2 float encoding, the sysfs identity and the persisted model. No X server needed.
"""
import pytest

from unitotem_node.models.touch_map import TouchMap
from unitotem_node.system import touch
from unitotem_node.system.touch import (
    IDENTITY, ROTATE_0, ROTATE_180, ROTATE_270, ROTATE_90, Output, TouchDevice,
)


def apply(matrix, x, y):
    """Where a normalised touch lands, normalised over the whole X screen."""
    return (matrix[0] * x + matrix[1] * y + matrix[2], matrix[3] * x + matrix[4] * y + matrix[5])


# ── identity ─────────────────────────────────────────────────────────────────

def test_identity_key_has_name_ids_and_serial():
    assert touch.identity_key('Foo Touch', 0x04f3, 0x2a1b, 'SN1') == 'Foo Touch|04f3:2a1b|SN1'


def test_identity_key_without_ids_or_serial():
    assert touch.identity_key(' Foo ', None, None, None) == 'Foo||'


def test_two_identical_panels_are_told_apart_by_port():
    a = TouchDevice(xid=11, name='Panel', vendor=1, product=2, phys='usb-1/input0')
    b = TouchDevice(xid=12, name='Panel', vendor=1, product=2, phys='usb-2/input0')
    c = TouchDevice(xid=13, name='Other', vendor=1, product=3)
    touch.assign_keys([a, b, c])
    assert a.key != b.key
    assert a.key.endswith('@usb-1/input0') and b.key.endswith('@usb-2/input0')
    assert c.key == 'Other|0001:0003|'


def test_key_does_not_depend_on_the_x_id():
    one = TouchDevice(xid=11, name='Panel', vendor=1, product=2, serial='S')
    two = TouchDevice(xid=47, name='Panel', vendor=1, product=2, serial='S')
    touch.assign_keys([one])
    touch.assign_keys([two])
    assert one.key == two.key


# ── matrix ───────────────────────────────────────────────────────────────────

def test_full_screen_output_is_identity():
    assert touch.output_matrix(Output('HDMI-1', 0, 0, 1920, 1080), 1920, 1080) == pytest.approx(IDENTITY)


def test_second_output_to_the_right():
    m = touch.output_matrix(Output('HDMI-2', 1920, 0, 1920, 1080), 3840, 1080)
    assert apply(m, 0, 0) == pytest.approx((0.5, 0))
    assert apply(m, 1, 1) == pytest.approx((1, 1))


def test_output_below_and_smaller():
    m = touch.output_matrix(Output('DP-1', 0, 1080, 1280, 720), 1920, 1800)
    assert apply(m, 0, 0) == pytest.approx((0, 0.6))
    assert apply(m, 1, 1) == pytest.approx((1280 / 1920, 1))


@pytest.mark.parametrize('rotation, native_corner, lands_on', [
    # the panel's own top-left corner ends up where the rotated picture's corner is
    (ROTATE_0, (0, 0), (0, 0)),
    (ROTATE_90, (0, 0), (1, 0)),        # xrandr 'left'
    (ROTATE_180, (0, 0), (1, 1)),       # 'inverted'
    (ROTATE_270, (0, 0), (0, 1)),       # 'right'
    (ROTATE_90, (1, 1), (0, 1)),
    (ROTATE_270, (1, 1), (1, 0)),
])
def test_rotation_moves_the_corners(rotation, native_corner, lands_on):
    m = touch.output_matrix(Output('DP-1', 0, 0, 1080, 1920, rotation), 1080, 1920)
    assert apply(m, *native_corner) == pytest.approx(lands_on)


def test_rotated_output_on_the_right_of_another():
    # 1920x1080 on the left, a second one turned 'left' (so 1080x1920 in the layout) on its right
    m = touch.output_matrix(Output('DP-2', 1920, 0, 1080, 1920, ROTATE_90), 3000, 1920)
    x, y = apply(m, 0, 0)
    assert (x, y) == pytest.approx((3000 / 3000, 0))
    x, y = apply(m, 1, 1)
    assert (x, y) == pytest.approx((1920 / 3000, 1))


def test_degenerate_geometry_gives_identity():
    assert touch.output_matrix(Output('X', 0, 0, 0, 0), 100, 100) == IDENTITY
    assert touch.output_matrix(Output('X', 0, 0, 10, 10), 0, 0) == IDENTITY


# ── plan ─────────────────────────────────────────────────────────────────────

def _devices():
    return touch.assign_keys([TouchDevice(xid=10, name='A', vendor=1, product=1),
                              TouchDevice(xid=11, name='B', vendor=1, product=2)])


def test_plan_maps_only_mapped_devices():
    outputs = [Output('HDMI-1', 0, 0, 1000, 1000), Output('HDMI-2', 1000, 0, 1000, 1000)]
    devices = _devices()
    result = touch.plan(devices, {devices[0].key: 'HDMI-2'}, outputs, (2000, 1000))
    assert set(result) == {10}
    assert apply(result[10], 0, 0) == pytest.approx((0.5, 0))


def test_plan_resets_a_device_whose_mapping_was_removed():
    devices = _devices()
    result = touch.plan(devices, {}, [], (100, 100), managed={devices[1].key})
    assert result == {11: IDENTITY}


def test_plan_unplugged_output_falls_back_to_identity():
    devices = _devices()
    result = touch.plan(devices, {devices[0].key: 'DP-9'}, [Output('HDMI-1', 0, 0, 10, 10)], (10, 10))
    assert result == {10: IDENTITY}


# ── XI2 float encoding ───────────────────────────────────────────────────────

def test_float_property_round_trip():
    m = (0.5, 0.0, 0.25, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    words = touch.matrix_to_property(m)
    assert words[0] == 0x3F000000           # 0.5 as an IEEE-754 single
    assert touch.matrix_from_property(words) == pytest.approx(m)


def test_close_enough():
    assert touch.close_enough(IDENTITY, tuple(v + 1e-6 for v in IDENTITY))
    assert not touch.close_enough(IDENTITY, (0.5,) + IDENTITY[1:])
    assert not touch.close_enough(IDENTITY, IDENTITY[:8])


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
    assert found == {'phys': 'usb-0000:00:14.0-2/input0', 'serial': 'ABC123'}


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


# ── no X ─────────────────────────────────────────────────────────────────────

def test_snapshot_without_x_is_empty():
    from unitotem_node.system.touch_service import TouchService
    s = TouchService().snapshot()
    assert s['available'] is False and s['outputs'] == []
