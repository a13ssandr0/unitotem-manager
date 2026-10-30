"""
Touch-to-screen mapping: which output each touch screen belongs to.

The compositor does the mapping (labwc's `<touch deviceName=... mapToOutput=.../>`, which also follows the output's position
and rotation by itself), so what is left here is knowing the touch screens and writing that part of the configuration:

* the touch screens are found in `/proc/bus/input/devices`: an input device with the DIRECT property (a touchpad does not
  have it, it is not a screen) that reports absolute positions (multi-touch or single touch),
* identity is name, vendor:product and serial (sysfs), never the `eventN` number, which changes at every boot,
* the mapping is the node's fragment of the compositor configuration (utils/labwc.py), read again on SIGHUP.

labwc picks a device by its NAME: two panels of the same model share one mapping (the first one in the file wins), which is
a limit of the compositor; the page says so by showing both with the same output.

The module is importable and harmless without a compositor or any touch device: every entry point answers "nothing there".
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

PROC_DEVICES = '/proc/bus/input/devices'
INPUT_PROP_DIRECT = 1       # bit number: the device is a screen (a touch screen), not a pointer on a surface
ABS_X, ABS_MT_POSITION_X = 0, 0x35
FRAGMENT = 'node'


@dataclass
class TouchDevice:
    name: str
    vendor: Optional[int] = None
    product: Optional[int] = None
    serial: Optional[str] = None
    phys: Optional[str] = None       # USB port path: tells apart two identical panels
    node: Optional[str] = None       # /dev/input/eventN, only used to look the serial up
    key: str = ''                    # stable identity, set by assign_keys()


# ── pure logic ───────────────────────────────────────────────────────────────

def identity_key(name: str, vendor: Optional[int], product: Optional[int], serial: Optional[str]) -> str:
    """Stable identity of a touch device: name, vendor:product and serial, whichever are known."""
    ids = f'{vendor:04x}:{product:04x}' if vendor is not None and product is not None else ''
    return '|'.join((name.strip(), ids, (serial or '').strip()))


def assign_keys(devices: list[TouchDevice]) -> list[TouchDevice]:
    """Set `key` on every device. Two devices with the same identity (two panels of the same model and no serial number)
    are told apart by the port they are plugged into, which survives reboots as long as the cable stays where it is."""
    base = [identity_key(d.name, d.vendor, d.product, d.serial) for d in devices]
    counts: dict[str, int] = {}
    for key in base:
        counts[key] = counts.get(key, 0) + 1
    for index, d in enumerate(devices):
        key = base[index]
        if counts[key] > 1:
            key += '|@' + (d.phys or f'#{index}')
        d.key = key
    return devices


def _bit(bitmap: str, number: int) -> bool:
    """Bit `number` of a /proc/bus/input/devices bitmap: hexadecimal words of the machine's long size, most significant first."""
    words = bitmap.split()
    size = 64 if any(len(w) > 8 for w in words) else 32          # not exact for a short map, which never reaches these bits
    word, offset = divmod(number, size)
    if word >= len(words):
        return False
    return bool(int(words[-1 - word], 16) >> offset & 1)


def parse_devices(text: str) -> list[TouchDevice]:
    """The touch screens in the contents of /proc/bus/input/devices."""
    devices = []
    for block in re.split(r'\n\s*\n', text.strip()):
        fields: dict[str, str] = {}
        name = ''
        ids = {}
        for line in block.splitlines():
            kind, _, rest = line.partition(': ')
            if kind == 'I':
                ids = dict(part.split('=', 1) for part in rest.split() if '=' in part)
            elif kind == 'N':
                m = re.match(r'Name="(.*)"', rest)
                name = m[1] if m else ''
            elif kind in ('P', 'U', 'H', 'B'):
                label, _, value = rest.partition('=')
                fields[f'{kind}:{label}'] = value.strip()
        props, absmap = fields.get('B:PROP', '0'), fields.get('B:ABS', '0')
        if not name or not _bit(props, INPUT_PROP_DIRECT) or not (_bit(absmap, ABS_MT_POSITION_X) or _bit(absmap, ABS_X)):
            continue
        event = next((h for h in fields.get('H:Handlers', '').split() if h.startswith('event')), None)
        try:
            vendor, product = int(ids['Vendor'], 16), int(ids['Product'], 16)
        except (KeyError, ValueError):
            vendor = product = None
        uniq = fields.get('U:Uniq') or None
        devices.append(TouchDevice(name=name, vendor=vendor, product=product, serial=uniq,
                                   phys=fields.get('P:Phys') or None, node=f'/dev/input/{event}' if event else None))
    return assign_keys(devices)


_EVENT_RE = re.compile(r'^/dev/input/(event\d+)$')


def sysfs_identity(device_node: Optional[str], root: str = '/sys/class/input') -> dict:
    """Serial of an input device, from sysfs: a touch screen that has one has it on its USB device, a few directories above
    the input node."""
    m = _EVENT_RE.match(device_node or '')
    if not m:
        return {}
    try:
        node = os.path.realpath(os.path.join(root, m[1], 'device'))
    except OSError:
        return {}
    found = {}
    uniq = _read(os.path.join(node, 'uniq'))
    if uniq:
        found['serial'] = uniq
    if 'serial' not in found:
        parent = os.path.dirname(node)
        for _ in range(5):                  # HID interface -> USB interface -> USB device
            serial = _read(os.path.join(parent, 'serial'))
            if serial and os.path.exists(os.path.join(parent, 'idVendor')):
                found['serial'] = serial
                break
            if parent in ('/', '/sys'):
                break
            parent = os.path.dirname(parent)
    return found


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def touch_devices(text: Optional[str] = None) -> list[TouchDevice]:
    """The touch screens of this machine (empty when /proc has none, or on a machine with no input at all)."""
    if text is None:
        try:
            with open(PROC_DEVICES) as f:
                text = f.read()
        except OSError:
            return []
    devices = parse_devices(text)
    for d in devices:
        if d.serial is None:
            d.serial = sysfs_identity(d.node).get('serial')
    return assign_keys(devices)


def effective_mapping(mapping: dict[str, str], devices: list[TouchDevice], outputs: list[str]) -> dict[str, str]:
    """What is written for the compositor: the mapping the operator chose, plus, with ONE active output, every touch device that has
    none (it can only belong to that screen). Without this a device on automatic spans the layout as if the output were not turned
    or mirrored, and a touch lands on the wrong place. With several outputs nothing can be guessed: automatic stays automatic."""
    result = dict(mapping)
    if len(outputs) == 1:
        for device in devices:
            result.setdefault(device.key, outputs[0])
    return result


def fragment(mapping: dict[str, str]) -> str:
    """The `<touch>` entries of the node's fragment: one per mapped device, by name. Entries whose output is not there
    are harmless (the compositor then lets the device span every output); an identity that is not a device name is
    skipped. The first mapping of a name wins, the rest cannot be told apart by the compositor."""
    seen = set()
    lines = []
    for key, output in mapping.items():
        name = key.split('|')[0].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(f'  <touch deviceName="{html.escape(name, quote=True)}" mapToOutput="{html.escape(output, quote=True)}" '
                     'mouseEmulation="no"/>\n')
    return ''.join(lines)


def apply_mapping(mapping: dict[str, str]) -> bool:
    """Write the node's fragment of the compositor configuration and have it read again. True when a compositor took it."""
    from utils import labwc
    if not labwc.write_fragment(FRAGMENT, fragment(mapping)):
        return False
    reloaded = labwc.reload()
    if reloaded:
        logger.info('Touch mapping applied: {}', {k.split('|')[0]: v for k, v in mapping.items()} or 'none')
    return reloaded
