"""The raw taps of a panel and the guided setup service built on them: a FIFO stands for the panel's event node."""
import asyncio
import os
import struct

import pytest

from plugins.unitotem_node.models import touch_map as touch_map_module
from plugins.unitotem_node.models.touch_map import TouchMap
from plugins.unitotem_node.system import touch, touch_calibration as tc, touch_setup as ts
from plugins.unitotem_node.system.screens import wayland

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
MAX = 4095


def event(kind, code, value):
    return struct.pack('qqHHi', 0, 0, kind, code, value)


def tap_events(x, y):
    """A single-touch panel's tap at (x, y) in 0..1 of its range: position, touch down, frame, touch up, frame."""
    return b''.join([event(EV_ABS, 0, round(x * MAX)), event(EV_ABS, 1, round(y * MAX)), event(EV_KEY, ts.BTN_TOUCH, 1), event(EV_SYN, 0, 0),
                     event(EV_KEY, ts.BTN_TOUCH, 0), event(EV_SYN, 0, 0)])


def feed_all(detector, events):
    out = []
    for offset in range(0, len(events), ts.EVENT.size):
        _s, _u, kind, code, value = ts.EVENT.unpack_from(events, offset)
        point = detector.feed(kind, code, value)
        if point:
            out.append(point)
    return out


def test_a_single_touch_tap_is_reported_when_the_finger_leaves():
    taps = feed_all(ts.TapDetector({0: (0, MAX), 1: (0, MAX)}), tap_events(0.25, 0.75))
    assert len(taps) == 1 and abs(taps[0][0] - 0.25) < 1e-3 and abs(taps[0][1] - 0.75) < 1e-3


def test_the_position_is_the_middle_of_the_contact_not_where_the_finger_slid_off():
    d = ts.TapDetector({0: (0, MAX), 1: (0, MAX)})
    events = b''.join([event(EV_KEY, ts.BTN_TOUCH, 1)] + [event(EV_ABS, 0, round(v * MAX)) + event(EV_ABS, 1, round(0.5 * MAX)) + event(EV_SYN, 0, 0)
                                                           for v in (0.40, 0.41, 0.42, 0.43, 0.9)] + [event(EV_KEY, ts.BTN_TOUCH, 0)])
    (x, y), = feed_all(d, events)
    assert 0.40 <= x <= 0.43 and abs(y - 0.5) < 1e-3


def test_a_range_that_does_not_start_at_zero_is_normalised():
    taps = feed_all(ts.TapDetector({0: (100, 1100), 1: (200, 800)}), event(EV_ABS, 0, 600) + event(EV_ABS, 1, 500) + event(EV_KEY, ts.BTN_TOUCH, 1)
                    + event(EV_SYN, 0, 0) + event(EV_KEY, ts.BTN_TOUCH, 0))
    assert taps == [(0.5, 0.5)]


def test_a_multitouch_tap_ends_with_its_tracking_id_and_a_second_finger_is_ignored():
    d = ts.TapDetector({ts.ABS_MT_X: (0, MAX), ts.ABS_MT_Y: (0, MAX)})
    events = b''.join([
        event(EV_ABS, ts.ABS_MT_SLOT, 0), event(EV_ABS, ts.ABS_MT_ID, 7), event(EV_ABS, ts.ABS_MT_X, round(0.2 * MAX)),
        event(EV_ABS, ts.ABS_MT_Y, round(0.3 * MAX)), event(EV_SYN, 0, 0),
        event(EV_ABS, ts.ABS_MT_SLOT, 1), event(EV_ABS, ts.ABS_MT_ID, 8), event(EV_ABS, ts.ABS_MT_X, round(0.9 * MAX)),
        event(EV_ABS, ts.ABS_MT_Y, round(0.9 * MAX)), event(EV_SYN, 0, 0),
        event(EV_ABS, ts.ABS_MT_ID, -1),                                  # slot 1 lifts: not the first finger
        event(EV_ABS, ts.ABS_MT_SLOT, 0), event(EV_ABS, ts.ABS_MT_ID, -1)])
    taps = feed_all(d, events)
    assert len(taps) == 1 and abs(taps[0][0] - 0.2) < 1e-3 and abs(taps[0][1] - 0.3) < 1e-3


# ── the service ───────────────────────────────────────────────────────────────

class FakeService:
    def __init__(self, node):
        self.devices = [touch.TouchDevice(name='Panel', vendor=1, product=2, node=node, key='Panel|0001:0002|')]
        self.outputs = [wayland.Output(name='HDMI-A-1', connected=True, active=True, width=1920, height=1080, transform='normal')]
        self.trial = None
        self.refreshes = []

    async def refresh(self):
        self.refreshes.append(self.trial)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    fresh = TouchMap()
    monkeypatch.setattr(touch_map_module, 'touch_map', fresh)
    monkeypatch.setattr(TouchMap, 'save', lambda self: None)
    fifo = tmp_path / 'event0'
    os.mkfifo(fifo)
    service = FakeService(str(fifo))
    notified = []

    async def notify(state):
        notified.append(state)

    service_setup = ts.TouchSetup(service)
    service_setup.bind(notify)
    return service_setup, service, fresh, fifo, notified


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)


def writer(fifo):
    return os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)


def test_the_four_corners_then_try_then_keep(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        state = await service_setup.start(service.devices[0].key, 'corners')
        assert state['phase'] == 'running' and state['total'] == 4 and state['spot'] == {'x': 0.12, 'y': 0.12}
        panel = touch.CALIBRATION[(1, 0)]                                 # a panel turned a quarter against its screen
        fd = writer(fifo)
        for point in [tc.apply(tc.invert(panel), t) for t in tc.CORNER_TARGETS]:
            os.write(fd, tap_events(*point))
            await settle()
        assert service_setup.session.phase == 'try'
        assert service.trial is not None and tuple(service.trial[1]) == panel          # the compositor is given the result, nothing is stored
        assert touch_map.orientation == {} and touch_map.calibration == {}
        assert notified[-1]['phase'] == 'try'
        await service_setup.confirm()
        assert touch_map.orientation[service.devices[0].key] == {'turn': 1, 'flip': 0}
        assert service.trial is None and notified[-1]['phase'] == 'saved' and service_setup.session is None
        os.close(fd)
    asyncio.run(go())


def test_five_points_store_a_calibration_matrix(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        await service_setup.start(service.devices[0].key, 'points')
        true = (0.95, 0.0, 0.02, 0.0, 1.04, -0.02)                         # a panel a little scaled and shifted
        fd = writer(fifo)
        for point in [tc.apply(tc.invert(true), t) for t in tc.POINT_TARGETS]:
            os.write(fd, tap_events(*point))
            await settle()
        assert service_setup.session.phase == 'try'
        await service_setup.confirm()
        matrix = touch_map.calibration[service.devices[0].key]
        assert all(abs(a - b) < 0.01 for a, b in zip(matrix, true)) and service.devices[0].key not in touch_map.orientation
        os.close(fd)
    asyncio.run(go())


def test_cancelling_keeps_nothing_and_gives_the_stored_configuration_back(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        await service_setup.start(service.devices[0].key, 'corners')
        fd = writer(fifo)
        for point in [tc.apply(tc.invert(touch.CALIBRATION[(2, 0)]), t) for t in tc.CORNER_TARGETS]:
            os.write(fd, tap_events(*point))
            await settle()
        assert service.trial is not None
        await service_setup.cancel()
        assert service.trial is None and service.refreshes[-1] is None            # refreshed without the trial
        assert touch_map.orientation == {} and service_setup.session is None and notified[-1]['phase'] == 'idle'
        os.close(fd)
    asyncio.run(go())


def test_a_failed_setup_says_why_and_can_be_started_again(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        await service_setup.start(service.devices[0].key, 'points')
        fd = writer(fifo)
        for _ in range(5):
            os.write(fd, tap_events(0.5, 0.5))
            await settle()
        assert service_setup.session.phase == 'error' and 'same place' in notified[-1]['message']
        with pytest.raises(ts.SetupRefused):
            await service_setup.confirm()
        await service_setup.start(service.devices[0].key, 'corners')              # again, from the start
        assert service_setup.session.phase == 'running' and service_setup.session.step == 0
        await service_setup.cancel()
        os.close(fd)
    asyncio.run(go())


def test_it_does_not_start_for_a_panel_that_is_not_there_or_a_screen_nobody_chose(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        with pytest.raises(ts.SetupRefused, match='not connected'):
            await service_setup.start('nothing|0000:0000|', 'corners')
        service.outputs.append(wayland.Output(name='DP-1', connected=True, active=True))
        with pytest.raises(ts.SetupRefused, match='Choose the screen'):
            await service_setup.start(service.devices[0].key, 'corners')
        with pytest.raises(ts.SetupRefused):
            await service_setup.start(service.devices[0].key, 'sideways')
    asyncio.run(go())


def test_a_setup_ends_when_its_panel_is_unplugged(setup):
    async def go():
        service_setup, service, touch_map, fifo, notified = setup
        await service_setup.start(service.devices[0].key, 'corners')
        key = service.devices[0].key
        service.devices.clear()
        await service_setup.panel_gone(key)
        assert service_setup.session is None
    asyncio.run(go())


def test_a_calibration_is_what_the_compositor_is_told_and_it_replaces_the_orientation():
    key = 'Panel|0001:0002|'
    m = TouchMap()
    m.set_orientation(key, 1, 0)
    m.set_calibration(key, [0.9, 0, 0.05, 0, 1.1, -0.05])
    assert m.orientation == {} and m.calibration[key] == [0.9, 0, 0.05, 0, 1.1, -0.05]
    text = touch.fragment({key: 'HDMI-A-1'}, m.orientation, {key: 'Panel'}, m.calibration)
    assert '0.9 0.0 0.05 0.0 1.1 -0.05' in text
    m.set_orientation(key, 2, 0)
    assert m.calibration == {}
    for bad in ([1, 2, 3], [float('nan')] * 6, ['a'] * 6, [1e9] * 6, True):
        with pytest.raises(ValueError):
            m.set_calibration(key, bad)
