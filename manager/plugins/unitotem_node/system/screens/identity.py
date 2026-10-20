"""
Pure logic for joining a compositor output name to the physical monitor identity ddcutil reports for an I2C bus.

No subprocess calls, no sysfs reads here (see wayland.py for those): an EDID decoder and an output-name normaliser.

WHY EDID: ddcutil names a monitor by its EDID (`mfg:model:serial`), so the join is: output name -> the kernel's
`/sys/class/drm/card*-<output>/edid` (the compositor names its outputs after the DRM connectors, wlroots reads them from there) ->
edid_identity() -> matched against ddc.DetectedDisplay.monitor. The connector may have no EDID file on a driver that does
not populate it (NVIDIA's proprietary one has no per-connector directories at all): then the identity of that output could
not be established, which is an outcome and not a bug, and the join falls back to the connector name ddcutil reports.
"""
from __future__ import annotations

import re
from typing import Optional

_MAGIC = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])

# EDID 1.x descriptor blocks: four 18-byte slots at these fixed offsets. A
# "monitor descriptor" (not a detailed timing) has its first three bytes
# zero; byte 3 is the descriptor tag; 0xFC = display product name, 0xFF =
# display product serial number, both ASCII, newline-terminated/padded.
_DESCRIPTOR_OFFSETS = (54, 72, 90, 108)
_TAG_NAME = 0xFC
_TAG_SERIAL = 0xFF


def edid_identity(edid: bytes) -> Optional[str]:
    """'MFG:Model:Serial' from raw EDID bytes, matching the identity string
    ddcutil's own `detect`/`detect --brief` prints on its 'Monitor:' line -
    that string is the join key against ddc.DetectedDisplay.monitor.

    Returns None for anything too short or not EDID at all (wrong magic
    number) - a disconnected output, a monitor with no EDID, or garbage.
    Model/serial individually default to None if their descriptor block is
    absent (some monitors only carry one of the two); the mfg ID is always
    present in a valid EDID header, so its absence is what makes this return
    None outright rather than a partial string.

    Verified against three real, distinct captures: the LG Ultrafine on
    unitotem-test's DP-1 (via xrandr --props) and the dev host's own
    HDMI-A-1/DP-1 outputs (via /sys/class/drm/<conn>/edid) - all decode to
    the manufacturer/model/serial ddcutil independently reported for the same
    bus.
    """
    if len(edid) < 128 or edid[:8] != _MAGIC:
        return None

    # Bytes 8-9, big-endian: bit 15 always 0, then three 5-bit groups, each a
    # letter A-Z encoded as 1-26 (1='A'). This is the PNP-ID scheme every EDID
    # manufacturer field uses - 'GSM' (LG's PNP ID), 'PHL' (Philips) in the
    # fixtures this was verified against.
    packed = (edid[8] << 8) | edid[9]
    mfg = ''.join(chr(((packed >> shift) & 0x1F) + ord('A') - 1) for shift in (10, 5, 0))

    model = serial = None
    for offset in _DESCRIPTOR_OFFSETS:
        block = edid[offset:offset + 18]
        if len(block) < 18 or block[0:3] != b'\x00\x00\x00':
            continue
        tag = block[3]
        if tag not in (_TAG_NAME, _TAG_SERIAL):
            continue
        text = block[5:18].split(b'\n', 1)[0].decode('ascii', 'ignore').strip()
        if tag == _TAG_NAME:
            model = text
        else:
            serial = text

    return f'{mfg}:{model}:{serial}'


from utils.outputs import canonical_output_name  # noqa: E402,F401  (re-exported: it used to live here)


def same_output(randr_name: str, drm_connector: str) -> bool:
    """Whether a RandR output name and a DRM connector name refer to the same
    physical output, per canonical_output_name()."""
    return canonical_output_name(randr_name) == canonical_output_name(drm_connector)
