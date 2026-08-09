"""
Parser tests against real captured ddcutil output - two versions, two
machines: 2.2.5 (dev host, three physical monitors) and 2.2.0 (unitotem-test,
trixie's packaged version, GTX 1060 passthrough). See
manager/utils/system/screens/ddc.py's module docstring and
SCREEN-CONTROL-PLAN.md sections 1.1/6/10 for the full story.
"""
import pytest

from utils.system.screens.ddc import (
    BusNotFound,
    CommunicationFailed,
    parse_capabilities,
    parse_detect,
    parse_getvcp_terse,
    parse_version,
    supports_ddc,
)


# ── detect --brief ──────────────────────────────────────────────────────────

def test_detect_brief_225_three_displays(fixtures):
    text = (fixtures / 'ddcutil_detect_brief_225.txt').read_text()
    displays = parse_detect(text)
    assert len(displays) == 3

    philips, hdmi_lg, dp_lg = displays

    # The Philips is what ddcutil calls "Invalid display" - kept, not skipped,
    # and specifically NOT interpreted as "no monitor here": on the dev host
    # this exact monitor answered 0x10/0x60/0x62/0x8D fine and only failed on
    # 0xD6. See ddc.DetectedDisplay's docstring for why filtering valid=False
    # out would be the bug this test guards against.
    assert philips.valid is False
    assert philips.bus == 7
    assert philips.connector == 'card1-DVI-D-1'
    assert philips.monitor == 'PHL:PHL 246V5:AU01626003817'

    assert hdmi_lg.valid is True
    assert hdmi_lg.bus == 8
    assert hdmi_lg.connector == 'card1-HDMI-A-1'
    assert hdmi_lg.monitor == 'GSM:LG ULTRAFINE:406NTJJ6D028'

    assert dp_lg.valid is True
    assert dp_lg.bus == 9
    assert dp_lg.connector == 'card1-DP-1'
    assert dp_lg.monitor == 'GSM:LG ULTRAFINE:401NTMX52747'


def test_detect_brief_accepts_both_connector_spellings():
    # `detect --brief` spells it 'DRM connector:'; plain `detect` spells it
    # 'DRM_connector:'. Both seen across ddcutil invocations/versions.
    text = 'Display 1\n   I2C bus: /dev/i2c-1\n   DRM_connector: card0-DP-1\n'
    displays = parse_detect(text)
    assert displays[0].connector == 'card0-DP-1'


def test_detect_brief_220_no_connector_line_real_capture(fixtures):
    # Real capture, unitotem-test, ddcutil 2.2.0 under the NVIDIA driver: it
    # cannot resolve a DRM connector for this bus at all, so there is no
    # 'DRM connector:' line - confirming the EDID join (identity.py) is load-
    # bearing on this hardware, not just defensive extra coverage. ~10 lines
    # of connector-resolution diagnostics precede the real Display block on
    # stdout; the parser must not be thrown by them.
    text = (fixtures / 'ddcutil_detect_brief_220.txt').read_text()
    displays = parse_detect(text)
    assert len(displays) == 1
    d = displays[0]
    assert d.valid is True
    assert d.bus == 4
    assert d.connector is None
    assert d.monitor == 'GSM:LG ULTRAFINE:401NTMX52747'


# ── getvcp --terse ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('line,code,kind,value,max_value', [
    ('VCP 62 C 30 100',            '62', 'C',   30,  100),
    ('VCP 62 C 81 100',            '62', 'C',   81,  100),
    ('VCP 62 C 100 100',           '62', 'C',   100, 100),
    ('VCP 10 C 19 100',            '10', 'C',   19,  100),
    ('VCP 60 SNC x00',             '60', 'SNC', 0,   None),
    ('VCP 60 SNC x11',             '60', 'SNC', 17,  None),
    ('VCP D6 SNC x01',             'D6', 'SNC', 1,   None),
    ('VCP 8D CNC x00 x02 x00 x02', '8D', 'CNC', 2,   2),
    # Field order pinned here: max is 0x5DDC, current is 0x0000 - the two
    # pairs differ, so a swapped implementation would fail this specific row.
    ('VCP 0B CNC x5d xdc x00 x00', '0B', 'CNC', 0,   0x5DDC),
])
def test_getvcp_terse_225_rows(line, code, kind, value, max_value):
    result = parse_getvcp_terse(line)
    assert result is not None
    assert result.code == code
    assert result.kind == kind
    assert result.value == value
    assert result.max_value == max_value


def test_getvcp_terse_unsupported_code_is_none_not_an_error():
    # 'VCP AA ERR', dev host, exit 1 - a code this monitor does not support is
    # an expected outcome of probing, not a failure to raise about.
    assert parse_getvcp_terse('VCP AA ERR') is None


def test_getvcp_terse_bus_not_found_raises():
    with pytest.raises(BusNotFound):
        parse_getvcp_terse('Bus /dev/i2c-30 does not exist.')


def test_getvcp_terse_communication_failed_raises():
    # Real capture, dev host, bus 7 (the same Philips that answers other
    # codes fine) - no VCP line at all when this happens, just this message.
    with pytest.raises(CommunicationFailed):
        parse_getvcp_terse('DDC communication failed for monitor on bus /dev/i2c-7')


def test_getvcp_terse_survives_220_stdout_noise_real_capture(fixtures):
    # Real capture: 'ddcutil --bus 4 getvcp --terse D6' on ddcutil 2.2.0,
    # unitotem-test - ten lines of connector-resolution diagnostics on stdout
    # ahead of the actual VCP line. Redirecting stderr away does NOT remove
    # this; it is all on stdout. The parser must scan for the prefix, not
    # assume position. Value is x04 ("DPM: Off, DPMS: Off") rather than x01:
    # an earlier capture in the same session read x01, and this one, taken
    # later with the node running the headless stopgap (no CEF window driving
    # the output), read x04 - a real observed state change on the physical
    # monitor, not a capture error. Irrelevant to what this test checks,
    # which is only that the noise does not defeat the parser.
    text = (fixtures / 'ddcutil_220_stdout_noise.txt').read_text()
    result = parse_getvcp_terse(text)
    assert result is not None
    assert (result.code, result.kind, result.value) == ('D6', 'SNC', 4)


def test_getvcp_terse_no_recognisable_line_returns_none():
    assert parse_getvcp_terse('some unexpected output\nwith no VCP line\n') is None


# ── capabilities ─────────────────────────────────────────────────────────────

def test_capabilities_225(fixtures):
    text = (fixtures / 'ddcutil_capabilities_lg_225.txt').read_text()
    caps = parse_capabilities(text)

    assert caps['60'] == {
        '11': 'HDMI-1', '12': 'HDMI-2', '0f': 'DisplayPort-1',
        '00': 'Unrecognized value',
    }
    # Only what this monitor actually advertises - not the full MCCS 01-05
    # power-mode range. Offering 02/03 ("suspend"/"off") here would be a
    # guess, not a capability; this is the regression test for that.
    assert caps['D6'] == {'01': 'DPM: On,  DPMS: Off', '04': 'DPM: Off, DPMS: Off'}
    # Present with no value list - a feature the monitor supports but whose
    # values are continuous/not enumerated. Presence is itself the signal;
    # these keys must not be dropped for being empty.
    assert caps['62'] == {}
    assert caps['8D'] == {}


def test_capabilities_220_real_capture_matches_225_shape(fixtures):
    # Same physical monitor, different ddcutil version and machine - proves
    # the parser (and the monitor's own advertised capabilities) are stable
    # across the version this project actually verified against.
    text = (fixtures / 'ddcutil_capabilities_lg_220.txt').read_text()
    caps = parse_capabilities(text)
    assert caps['60'] == {
        '11': 'HDMI-1', '12': 'HDMI-2', '0f': 'DisplayPort-1',
        '00': 'Unrecognized value',
    }
    assert caps['D6'] == {'01': 'DPM: On,  DPMS: Off', '04': 'DPM: Off, DPMS: Off'}
    assert caps['62'] == {}
    assert caps['8D'] == {}


# ── version gate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('text,expected', [
    # (major, minor) - the third component (patch) is deliberately not part
    # of the return value, since supports_ddc() only ever needs >= (2, 0).
    ('ddcutil 2.2.5', (2, 2)),
    # Real capture: unitotem-test's actual packaged binary identifies itself
    # as '2.2.0-dev', not the plain '2.2.0' dpkg reports - still (2, 2).
    ('ddcutil 2.2.0-dev', (2, 2)),
    ('ddcutil 1.4.1-1', (1, 4)),
    ('command not found', None),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize('version,expected', [
    ((2, 0), True),
    ((2, 2), True),
    ((1, 4), False),   # bookworm's packaged version - the one this project
                       # explicitly chose not to support (moved the kiosk
                       # image to trixie instead); must degrade, not crash.
    (None, False),
])
def test_supports_ddc(version, expected):
    assert supports_ddc(version) is expected
