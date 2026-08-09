"""
Tests for the RandR->monitor-identity join. See
manager/utils/system/screens/identity.py's module docstring for why this
exists and what was actually verified on real hardware (the EDID-property
route, and why it is driver/topology-dependent - confirmed both ways: it
worked on the kiosk's own X session and failed, for an understood reason, on
this project's XWayland-backed dev host).
"""
from utils.system.screens.identity import (
    canonical_output_name,
    edid_identity,
    same_output,
    xrandr_edid_property,
)


# ── EDID -> mfg:model:serial ────────────────────────────────────────────────

def test_edid_identity_host_hdmi(fixtures):
    edid = (fixtures / 'edid_host_hdmi.bin').read_bytes()
    assert edid_identity(edid) == 'GSM:LG ULTRAFINE:406NTJJ6D028'


def test_edid_identity_host_dp(fixtures):
    edid = (fixtures / 'edid_host_dp.bin').read_bytes()
    assert edid_identity(edid) == 'GSM:LG ULTRAFINE:401NTMX52747'


def test_edid_identity_vm_dp1_matches_ddcutils_own_report(fixtures):
    # This is the join proven end to end: the identity decoded here from a
    # real xrandr EDID property is byte-identical to the 'Monitor:' line
    # ddcutil's own `detect --brief` printed for the same physical monitor's
    # I2C bus on the same machine (see ddcutil_detect_brief_220.txt) - not
    # merely the same shape, the literal same string.
    edid = (fixtures / 'edid_dp1_xrandr.bin').read_bytes()
    assert edid_identity(edid) == 'GSM:LG ULTRAFINE:401NTMX52747'


def test_edid_identity_too_short_is_none():
    assert edid_identity(b'\x00\xff\xff') is None


def test_edid_identity_bad_magic_is_none():
    assert edid_identity(b'\x01' * 128) is None


def test_edid_identity_valid_header_no_descriptors_still_has_mfg():
    # A minimal, synthetic EDID: valid magic + mfg bytes, no descriptor
    # blocks at all (128 zero bytes after the header up to byte 128). Model
    # and serial are legitimately absent on some real monitors; the mfg ID
    # alone must still produce a partial identity, not None outright - only
    # a bad/missing header does that (see the two tests above).
    edid = bytearray(128)
    edid[0:8] = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
    edid[8:10] = bytes([0x1E, 0x6D])   # 'GSM', the same packing as the real fixtures
    assert edid_identity(bytes(edid)) == 'GSM:None:None'


# ── xrandr --props EDID property ────────────────────────────────────────────

def test_xrandr_edid_property_real_capture_matches_ddcutil(fixtures):
    text = (fixtures / 'xrandr_props_dp1.txt').read_text()
    edid = xrandr_edid_property(text, 'DP-1')
    assert edid is not None
    assert edid_identity(edid) == 'GSM:LG ULTRAFINE:401NTMX52747'


def test_xrandr_edid_property_nonexistent_output_is_none(fixtures):
    text = (fixtures / 'xrandr_props_dp1.txt').read_text()
    assert xrandr_edid_property(text, 'HDMI-99') is None


def test_xrandr_edid_property_absent_on_real_xwayland_capture(fixtures):
    # Real capture, this project's own dev host: three connected outputs,
    # zero EDID properties anywhere - because :0 there is XWayland, whose
    # RandR is an emulation layer that never forwards EDID at all (see the
    # module docstring). Confirms the parser returns None for a genuinely
    # EDID-less capture rather than raising or fabricating something - the
    # exact shape the code will see on a developer's own machine.
    text = (fixtures / 'xrandr_props_host.txt').read_text()
    assert xrandr_edid_property(text, 'DP-1') is None
    assert xrandr_edid_property(text, 'HDMI-A-1') is None
    assert xrandr_edid_property(text, 'DVI-D-1') is None


# ── RandR <-> DRM name normalisation ─────────────────────────────────────────

def test_canonical_output_name_strips_card_prefix():
    assert canonical_output_name('card0-DP-1') == 'DP-1'
    assert canonical_output_name('card1-DVI-D-1') == 'DVI-D-1'


def test_canonical_output_name_folds_hdmi_a_form():
    # i915-style RandR name vs DRM connector name for the same output.
    assert canonical_output_name('HDMI-1') == 'HDMI-1'
    assert canonical_output_name('card0-HDMI-A-1') == 'HDMI-1'
    # NVIDIA-style: RandR itself already says 'HDMI-A-1' (confirmed real, dev
    # host) - normalising must be a no-op-equivalent that still lands on the
    # same canonical form as the i915 case above, not a different one.
    assert canonical_output_name('HDMI-A-1') == 'HDMI-1'
    assert canonical_output_name('card1-HDMI-A-1') == 'HDMI-1'


def test_canonical_output_name_leaves_others_alone():
    for name in ('DP-1', 'DVI-D-1', 'eDP-1', 'Virtual-2'):
        assert canonical_output_name(name) == name
        assert canonical_output_name(f'card0-{name}') == name


def test_same_output_matches_across_naming_conventions():
    assert same_output('HDMI-1', 'card0-HDMI-A-1') is True
    assert same_output('HDMI-A-1', 'card1-HDMI-A-1') is True
    assert same_output('DP-1', 'card1-DP-1') is True


def test_same_output_genuine_mismatch_is_false():
    assert same_output('HDMI-1', 'card0-DP-1') is False
