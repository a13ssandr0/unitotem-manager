"""Guided setup of a touch panel: the four corners and the five points, against synthetic panels turned, mirrored, scaled and shifted every way,
with the output turned and mirrored every way too (the picture the visitor sees is the output's transform of what the panel reports)."""
import random

import pytest

from plugins.unitotem_node.system import touch, touch_calibration as tc
from plugins.unitotem_node.system.screens import wayland

TRANSFORMS = sorted(wayland.TRANSFORMS)
PANELS = sorted({tuple(m) for m in touch.CALIBRATION.values()})        # the eight distinct matrices


def raw_taps(panel, transform, targets, noise=0.0, seed=1):
    """The raw positions a panel whose matrix is `panel` reports when the visitor touches `targets` on the picture of an output with `transform`."""
    rnd = random.Random(seed)
    native = [tc.apply(tc.invert(tc.output_matrix(transform)), t) for t in targets]
    return [tuple(v + rnd.uniform(-noise, noise) for v in tc.apply(tc.invert(panel), p)) for p in native]


def test_there_are_eight_panels_and_eight_transforms():
    assert len(PANELS) == 8 and len(TRANSFORMS) == 8


@pytest.mark.parametrize('transform', TRANSFORMS)
@pytest.mark.parametrize('panel', PANELS)
def test_the_four_corners_tell_every_panel_under_every_transform(panel, transform):
    taps = raw_taps(panel, transform, tc.CORNER_TARGETS, noise=0.06)
    turn, flip = tc.corners_orientation(taps, transform)
    assert tuple(touch.CALIBRATION[(turn, flip)]) == panel


def test_corners_touched_in_the_wrong_order_are_refused():
    taps = raw_taps(PANELS[0], 'normal', tc.CORNER_TARGETS)
    taps[1], taps[2] = taps[2], taps[1]
    with pytest.raises(tc.SetupError, match='wrong order'):
        tc.corners_orientation(taps)


def test_corners_all_in_one_place_are_refused():
    with pytest.raises(tc.SetupError):
        tc.corners_orientation([(0.2, 0.2)] * 4)


@pytest.mark.parametrize('transform', ['normal', '90', 'flipped-270'])
def test_five_points_recover_a_scaled_shifted_sheared_panel(transform):
    # a panel whose glass is 90 % of the width, shifted, a little sheared and turned a quarter: a frame on a whiteboard
    true = (0.0, -0.9, 0.96, 1.05, 0.03, -0.02)
    taps = raw_taps(true, transform, tc.POINT_TARGETS, noise=0.004)
    matrix = tc.points_matrix(taps, transform)
    for raw, target in zip(taps, tc.POINT_TARGETS):
        native = tc.apply(matrix, raw)
        picture = tc.apply(tc.output_matrix(transform), native)
        assert tc._distance(picture, target) < 0.03


def test_five_points_on_an_ordinary_panel_give_its_orientation_matrix():
    for panel in PANELS:
        taps = raw_taps(panel, 'normal', tc.POINT_TARGETS)
        matrix = tc.points_matrix(taps)
        assert all(abs(a - b) < 1e-4 for a, b in zip(matrix, panel))
        assert tc.orientation_of(matrix) is not None


def test_a_missed_target_is_refused():
    taps = raw_taps(PANELS[0], 'normal', tc.POINT_TARGETS)
    taps[4] = (0.95, 0.05)                              # the centre was touched in a corner
    with pytest.raises(tc.SetupError, match='missed'):
        tc.points_matrix(taps)


def test_the_same_place_five_times_is_refused():
    with pytest.raises(tc.SetupError, match='same place'):
        tc.points_matrix([(0.5, 0.5)] * 5)


def test_a_matrix_is_inverted_exactly():
    m = (0.0, -0.9, 0.96, 1.05, 0.03, -0.02)
    for p in [(0.1, 0.2), (0.9, 0.7)]:
        back = tc.apply(tc.invert(m), tc.apply(m, p))
        assert abs(back[0] - p[0]) < 1e-9 and abs(back[1] - p[1]) < 1e-9


def test_a_session_walks_through_its_targets_and_ends_in_a_result_to_try():
    session = tc.Session('corners', 'panel', 'HDMI-A-1', transform='90')
    taps = raw_taps(PANELS[3], '90', tc.CORNER_TARGETS)
    states = []
    for tap in taps:
        states.append(session.state())
        session.tap(tap)
    assert [s['step'] for s in states] == [0, 1, 2, 3] and states[0]['spot'] == {'x': 0.12, 'y': 0.12}
    assert session.phase == 'try' and tuple(session.matrix()) == PANELS[3]
    assert session.state()['spot'] is None


def test_a_session_that_cannot_conclude_says_why_and_can_be_started_again():
    session = tc.Session('points', 'panel', 'HDMI-A-1')
    for _ in range(5):
        session.tap((0.5, 0.5))
    assert session.phase == 'error' and 'same place' in session.message and session.result is None
    assert session.tap((0.1, 0.1)) == 'error'           # nothing more is taken once it has ended


def test_a_session_ends_by_itself_without_touches():
    session = tc.Session('corners', 'panel', 'HDMI-A-1')
    assert not session.expired()
    assert session.expired(session.last_touch + tc.STEP_TIMEOUT + 1)


def test_the_state_never_uses_a_name_the_websocket_protocol_owns():
    """The state is sent as the fields of a message (`target` is where the message goes): a key of that name made every call fail."""
    session = tc.Session('corners', 'panel', 'HDMI-A-1')
    for phase in ('running', 'try', 'error', 'saved', 'cancelled'):
        session.phase = phase
        assert not {'target', 'sessionId', 'error', 'extra'} & set(session.state())
