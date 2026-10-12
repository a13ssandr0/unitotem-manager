"""
Pure parsing of ddcutil's text output. No subprocess calls here - see the
module docstring in __init__.py for why, and SCREEN-CONTROL-PLAN.md step 4 for
where the subprocess-calling counterparts (detect(), capabilities(), getvcp(),
setvcp() - async, wrapped in asyncio.to_thread, one asyncio.Lock per I2C bus)
will live.

Every parser here was written against and re-verified byte-for-byte against
two real ddcutil versions on real hardware: 2.2.5 (dev host, three physical
monitors, DDC/CI capable) and 2.2.0 (unitotem-test, trixie's packaged version,
GTX 1060 passthrough, real 4K LG Ultrafine). See SCREEN-CONTROL-PLAN.md
sections 1.1 and 10 for the full story, including the one genuine surprise:
2.2.0 prints roughly ten lines of connector-resolution diagnostics to STDOUT
(not stderr) ahead of the real result whenever it cannot resolve a DRM
connector for the bus - which on the NVIDIA driver is always. Every parser
below scans for a recognisable prefix rather than assuming the result is on a
fixed line, which is what makes it survive that noise unmodified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


class DdcError(Exception):
    """A getvcp failure that is not simply 'this VCP code is unsupported' -
    that case (VCP xx ERR) is not an error at the protocol level and is
    reported as None, not raised. These are the two failure modes that ARE
    exceptional: a bus that does not exist, and a monitor that stopped
    responding on an otherwise-valid bus."""


class BusNotFound(DdcError):
    """'Bus /dev/i2c-N does not exist.' - the --bus argument named a bus with
    no I2C adapter at all. Confirmed real, dev host: `ddcutil --bus 30 getvcp
    --terse 62` -> exactly this line, exit 1."""


class CommunicationFailed(DdcError):
    """'DDC communication failed for monitor on bus /dev/i2c-N' - the bus
    exists and ddcutil's own `detect` may even have listed a monitor on it,
    but this specific VCP transaction got no reply. Confirmed real, dev host:
    the Philips 246V5 on bus 7 answered 0x10/0x60/0x62/0x8D fine but failed
    exactly this way on 0x6D - capability is per-VCP-code, not per-display,
    which is why ddcutil detect's own verdict ('Invalid display') must never
    be used as a capability signal (see identity.py and the plan's §1.1)."""


@dataclass(frozen=True)
class DetectedDisplay:
    """One entry from `ddcutil detect` / `detect --brief`.

    valid=False mirrors ddcutil's own "Invalid display" header: NOT a monitor
    to skip, since - per the CommunicationFailed note above - a monitor
    reported invalid by detect's own 0xDF probe can still answer other VCP
    codes perfectly well. Keep it in the list with valid=False; do not filter
    it out.
    """
    valid: bool
    bus: Optional[int]
    connector: Optional[str]      # None on any bus ddcutil can't resolve a DRM
                                   # connector for (confirmed real on the
                                   # NVIDIA/trixie passthrough config - not a
                                   # corner case there, the ONLY case) - the
                                   # EDID join (identity.py) is what covers it.
    monitor: Optional[str]        # 'MFG:Model:Serial', e.g.
                                   # 'GSM:LG ULTRAFINE:401NTMX52747' - the same
                                   # triple identity.edid_identity() decodes
                                   # from a raw EDID, which is the join key.


_DISPLAY_HEADER_RE = re.compile(r'^(Display \d+|Invalid display)$')


def parse_detect(text: str) -> list[DetectedDisplay]:
    """Parse `ddcutil detect` or `detect --brief` output (either verbosity;
    the `--brief` numbers below assume neither, only 'Display N' or 'Invalid
    display' as the record header, then indented 'I2C bus:', 'DRM connector:'
    / 'DRM_connector:' (both spellings seen across ddcutil versions), and
    'Monitor:' lines - `detect` without --brief adds an 'EDID synopsis:'
    block with the identity spread over 'Mfg id:'/'Model:'/'Serial number:'
    lines instead of one 'Monitor:' line; that fuller form is not parsed here
    since --brief is what the hardware layer will actually call, and its
    'Monitor:' line already gives the whole identity in one place.

    Any number of unrelated lines (connector-resolution diagnostics, on a bus
    ddcutil can't place - see the module docstring) may precede a record or
    sit between fields; only recognised prefixes are read.
    """
    out: list[DetectedDisplay] = []
    valid = bus = connector = monitor = None
    have_record = False

    def flush():
        if have_record:
            out.append(DetectedDisplay(valid=valid, bus=bus, connector=connector, monitor=monitor))

    for raw in text.splitlines():
        line = raw.strip()
        if _DISPLAY_HEADER_RE.match(line):
            flush()
            valid = (line != 'Invalid display')
            bus = connector = monitor = None
            have_record = True
            continue
        if not have_record:
            continue
        if line.startswith('I2C bus:'):
            m = re.search(r'/dev/i2c-(\d+)', line)
            bus = int(m.group(1)) if m else None
        elif line.startswith('DRM connector:') or line.startswith('DRM_connector:'):
            connector = line.split(':', 1)[1].strip()
        elif line.startswith('Monitor:'):
            monitor = line.split(':', 1)[1].strip()
    flush()
    return out


@dataclass(frozen=True)
class VcpValue:
    """One `getvcp --terse` result.

    kind is ddcutil's own vocabulary: 'C' (continuous - current/max are a
    meaningful range, e.g. volume), 'SNC' (simple non-continuous - current is
    one of a small set of named values, e.g. input source, power mode) or
    'CNC' (complex non-continuous - two 16-bit values, max then current).
    max_value is None for 'SNC', where there is no such notion.
    """
    code: str
    kind: str
    value: int
    max_value: Optional[int]


def parse_getvcp_terse(text: str) -> Optional[VcpValue]:
    """Parse one `ddcutil --bus N getvcp --terse <code>` result.

    Returns None for 'VCP xx ERR' (the code is not supported by this
    monitor - a normal, expected outcome to probe for, not a failure).
    Raises BusNotFound / CommunicationFailed for the two failure modes that
    ARE exceptional - see their docstrings. Returns None (rather than
    raising) if no recognisable line is found at all, since an unexpected
    ddcutil output should degrade to "nothing usable here", not crash the
    caller; the fixtures only exercise the outcomes actually observed on real
    hardware. Scans every line, tolerating ddcutil 2.2.0's ~10 lines of
    connector-resolution diagnostics ahead of the result on stdout - see the
    module docstring; also tolerates ddcutil's own extra progress/error
    output on stderr if the caller merges the streams instead of discarding
    stderr, though the recommended call keeps them separate.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('Bus ') and 'does not exist' in line:
            raise BusNotFound(line)
        if 'DDC communication failed' in line:
            raise CommunicationFailed(line)
        if not line.startswith('VCP '):
            continue
        fields = line.split()
        if len(fields) < 3:
            continue
        code, kind = fields[1], fields[2]
        if kind == 'ERR':
            return None
        if kind == 'C' and len(fields) >= 5:
            return VcpValue(code, kind, int(fields[3]), int(fields[4]))
        if kind == 'SNC' and len(fields) >= 4:
            return VcpValue(code, kind, int(fields[3].lstrip('x'), 16), None)
        if kind == 'CNC' and len(fields) >= 7:
            # Field order confirmed on real hardware, dev host: 'VCP 0B CNC
            # x5d xdc x00 x00' -> max 0x5DDC, cur 0x0000 - max-hi max-lo
            # cur-hi cur-lo, NOT the other way round.
            max_value = (int(fields[3].lstrip('x'), 16) << 8) | int(fields[4].lstrip('x'), 16)
            value     = (int(fields[5].lstrip('x'), 16) << 8) | int(fields[6].lstrip('x'), 16)
            return VcpValue(code, kind, value, max_value)
    return None


def parse_capabilities(text: str) -> dict[str, dict[str, str]]:
    """Parse `ddcutil capabilities` (or `-b N capabilities` / `-d N
    capabilities`) into {feature_code_hex_upper: {value_hex_lower: label}}.

    A feature with no 'Values:' sub-block (continuous features like 0x62
    audio volume, or 0x8D mute on some monitors) gets an empty dict, still
    present as a key - that presence is itself the "this monitor supports
    this feature" signal the hardware layer needs; do not drop empty entries.

    This is the ONLY reliable way to build an input-source menu or an offered
    power-mode list: the values a monitor supports for 0x60/0xD6 are monitor-
    specific, not fixed by the MCCS spec (confirmed real: the LG Ultrafine
    used for these fixtures advertises only 0x01/0x04 for 0xD6, not the full
    MCCS 0x01-0x05 range - offering 0x02/0x03 'suspend'/'off' states to a
    monitor that never advertised them would be a guess, not a capability).
    """
    feats: dict[str, dict[str, str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        m = re.match(r'^\s{3}Feature:\s+([0-9A-Fa-f]{2})\s', line)
        if m:
            current = m.group(1).upper()
            feats[current] = {}
            continue
        m = re.match(r'^\s{9}([0-9A-Fa-f]{2}):\s*(.+)$', line)
        if m and current is not None:
            feats[current][m.group(1).lower()] = m.group(2).strip()
    return feats


def parse_version(text: str) -> Optional[tuple[int, int]]:
    """(major, minor) from `ddcutil --version`'s first line, or None if it
    cannot be parsed at all (ddcutil not installed / unexpected output).

    Tolerates a trailing suffix after the two numbers: confirmed real,
    unitotem-test's actual packaged binary identifies itself as
    'ddcutil 2.2.0-dev' (not the '2.2.0' dpkg reports), and the dev host's
    bookworm docs describe '1.4.1-1'-style suffixes too - neither is a
    plain X.Y, so the regex only anchors the leading two numbers.
    """
    m = re.search(r'ddcutil\s+(\d+)\.(\d+)', text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def supports_ddc(version: Optional[tuple[int, int]]) -> bool:
    """Whether this ddcutil version is new enough to trust for this project.

    >= 2.0 is a hard requirement (SCREEN-CONTROL-PLAN.md §0): earlier
    versions were never verified and Debian bookworm's packaged 1.4.1 is
    exactly the version this project chose NOT to support (the kiosk image
    moved to trixie instead, which packages 2.2.0). A node still running an
    older ddcutil - a bookworm node that has not been reimaged yet, most
    likely - must have DDC/CI reported unavailable with this as the reason,
    not silently attempt it.
    """
    return version is not None and version >= (2, 0)
