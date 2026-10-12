"""
Pure logic for joining a RandR output name to the physical monitor identity
ddcutil reports for an I2C bus, and for comparing RandR and DRM connector
names when both happen to be readable.

No subprocess calls, no sysfs reads here - see the module docstring in
__init__.py. The impure counterparts (calling `xrandr --props`, reading
/sys/class/drm/<conn>/edid where it exists, enumerating outputs) are
SCREEN-CONTROL-PLAN.md step 4.

WHY EDID, AND WHY xrandr's OWN PROPERTY IS THE PRIMARY ROUTE - both established
on real hardware, not assumed (SCREEN-CONTROL-PLAN.md §1.3 and §10):
  - /sys/class/drm/<conn>/ddc (a fast path that would skip the manual decode
    below) does not exist on NVIDIA's proprietary driver at all - confirmed
    on the dev host.
  - Worse, confirmed later on unitotem-test's GTX 1060 passthrough under the
    same driver family: /sys/class/drm can have NO per-connector
    subdirectories whatsoever ('card0', 'renderD128', 'version' - nothing
    named after an actual connector), so there is no
    /sys/class/drm/<conn>/edid to fall back to either, not even the header.
  - What DOES work on the kiosk's actual runtime topology: `xrandr --props`
    exposes a raw EDID property on a connected output under a REAL Xorg
    session running directly on KMS with no compositor - exactly what the
    kiosk runs (no window manager, no Wayland - see CLAUDE.md's X11-no-WM
    migration entry) and exactly what unitotem-test's kiosk image runs.
    Decoded, it produced the exact 'GSM:LG ULTRAFINE:401NTMX52747' ddcutil's
    own `detect` printed for the same physical monitor's I2C bus - proven
    end to end on that VM, not merely plausible.
  - NOT universal, and the reason is now understood rather than a loose end:
    the dev host's own `:0` turned out to be XWayland (a compatibility X
    server running as a client of a Wayland compositor - `ps` shows
    `/usr/bin/Xwayland :0 ...`), and Xwayland's RandR is itself an emulation
    layer over Wayland's own output protocol (the tell is the
    'RANDR Emulation: 1' property `xrandr --props` reports on every output
    there) that does not forward EDID at all - confirmed real, zero `EDID:`
    properties anywhere in a full `xrandr --props` capture on that host, on
    three different connected outputs. This is irrelevant to the kiosk,
    which never runs Wayland or XWayland, but it means the CODE must treat
    "no EDID property returned" as "identity for this output could not be
    established" (try /sys/class/drm/<conn>/edid if present, else report the
    mechanism unavailable for that output), not as evidence of a bug -
    because a developer iterating against this code on their own desktop
    will hit exactly this path routinely.
So: RandR name -> xrandr --props' EDID property -> edid_identity() ->
mfg:model:serial -> matched against ddc.DetectedDisplay.monitor. That is the
join, verified end to end on the kiosk's own X topology. /sys/class/drm/
<conn>/edid, on a driver that does populate it, is accepted as an equally
valid source of the same bytes for the same decode step - not a different
design, just a cheaper way to get the same input on the drivers where it
exists.
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


def xrandr_edid_property(text: str, output_name: str) -> Optional[bytes]:
    """The raw EDID bytes `xrandr --props` reports for one output, or None if
    that output is not present in the text or has no EDID property (a
    disconnected output, or a driver that does not expose one).

    `xrandr --props` output looks like:

        DP-1 connected primary 3840x2160+0+0 ...
                EDID:
                        00ffffffffffff001e6dc15b...
                        ...
                non-desktop: 0

    i.e. one 'EDID:' line under the named output's block, followed by
    indented hex lines (no fixed count - length depends on how much of the
    EDID the driver exposes, 128 or 256 bytes both seen on real hardware)
    until the next non-hex line.
    """
    current: Optional[str] = None
    collecting = False
    chunks: list[str] = []
    found: dict[str, bytes] = {}

    def flush():
        if current is not None and chunks:
            found[current] = bytes.fromhex(''.join(chunks))

    for line in text.splitlines():
        m = re.match(r'^(\S+) (connected|disconnected)', line)
        if m:
            flush()
            current, collecting, chunks = m.group(1), False, []
            continue
        if re.match(r'^\s+EDID:\s*$', line):
            collecting = True
            continue
        if collecting and re.match(r'^\s+[0-9a-fA-F]{8,}\s*$', line):
            chunks.append(line.strip())
            continue
        collecting = False
    flush()

    return found.get(output_name)


_HDMI_A_RE = re.compile(r'^HDMI-A-(\d+)$')
_CARD_PREFIX_RE = re.compile(r'^card\d+-')


def canonical_output_name(name: str) -> str:
    """Fold a RandR output name and a DRM connector name that refer to the
    same physical output into one comparable form.

    Two independent differences observed on real hardware, both handled:
      - DRM connectors are prefixed 'cardN-' ('card0-HDMI-A-1'); RandR names
        never are ('HDMI-A-1'). Stripped unconditionally.
      - HDMI naming disagrees even between RandR and DRM on the SAME driver:
        confirmed real, dev host (NVIDIA): RandR calls it 'HDMI-A-1', DRM
        calls it 'card1-HDMI-A-1' - same 'HDMI-A-1' suffix, no problem. But
        on i915 (documented, not locally reproduced), RandR is reported to
        use the shorter 'HDMI-1' while DRM keeps 'HDMI-A-1'. Normalising
        'HDMI-A-n' -> 'HDMI-n' handles both conventions the same way, since
        applying it to an already-short 'HDMI-1' is a no-op.
    DP-n, DVI-D-n, eDP-n and Virtual-n have not been observed to disagree
    between RandR and DRM beyond the card prefix, so nothing else is touched.
    """
    stripped = _CARD_PREFIX_RE.sub('', name)
    m = _HDMI_A_RE.match(stripped)
    return f'HDMI-{m.group(1)}' if m else stripped


def same_output(randr_name: str, drm_connector: str) -> bool:
    """Whether a RandR output name and a DRM connector name refer to the same
    physical output, per canonical_output_name()."""
    return canonical_output_name(randr_name) == canonical_output_name(drm_connector)
