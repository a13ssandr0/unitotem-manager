"""
Touch-to-screen mapping: which output each touch screen belongs to.

Everything is done with python-xlib on the node's own X server, no `xinput`/`xrandr` command line:

* the touch screens are the slave pointers with a direct-touch class in `XIQueryDevice` (a touchpad is
  dependent-touch and is not a screen),
* the mapping is the `Coordinate Transformation Matrix` device property (what `xinput map-to-output` writes),
  computed here from the output geometry and rotation RandR reports,
* the layout is read with RandR (`get_crtc_info`), and changes are learnt from RandR notifications and XInput2
  hierarchy events, so nothing polls.

The module stays importable and harmless without an X server, without the XInput extension, or without any touch device:
every entry point answers "nothing there".
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

MATRIX_PROPERTY = 'Coordinate Transformation Matrix'
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

# XIQueryDevice 'use' of a slave pointer, and TouchClass mode of a screen (as opposed to a touchpad)
SLAVE_POINTER = 3
DIRECT_TOUCH = 1

# RandR rotation bits (Xlib.ext.randr.Rotate_*). Reflection is not handled: nothing in this project sets it, the content
# flip of the Display page is applied inside the page.
ROTATE_0, ROTATE_90, ROTATE_180, ROTATE_270 = 1, 2, 4, 8
_ROTATION_NAMES = {ROTATE_0: 'normal', ROTATE_90: 'left', ROTATE_180: 'inverted', ROTATE_270: 'right'}

# Rotation of the unit square, applied to the normalised touch position before it is scaled to the output rectangle.
# The same values `xinput map-to-output` uses; 'left' is RandR's Rotate_90.
_ROTATION_MATRICES = {
    ROTATE_0: IDENTITY,
    ROTATE_90: (0.0, -1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    ROTATE_180: (-1.0, 0.0, 1.0, 0.0, -1.0, 1.0, 0.0, 0.0, 1.0),
    ROTATE_270: (0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 1.0),
}


@dataclass(frozen=True)
class Output:
    name: str
    x: int
    y: int
    width: int
    height: int
    rotation: int = ROTATE_0

    @property
    def rotation_name(self) -> str:
        return _ROTATION_NAMES.get(self.rotation, 'normal')


@dataclass
class TouchDevice:
    xid: int                         # X device id: changes at every boot and hot-plug, never stored
    name: str
    vendor: Optional[int] = None
    product: Optional[int] = None
    serial: Optional[str] = None
    phys: Optional[str] = None       # USB port path: tells apart two identical panels
    key: str = ''                    # stable identity, set by assign_keys()
    matrix: tuple = field(default=IDENTITY)


# ── pure logic ───────────────────────────────────────────────────────────────

def identity_key(name: str, vendor: Optional[int], product: Optional[int], serial: Optional[str]) -> str:
    """Stable identity of a touch device: name, vendor:product and serial, whichever are known."""
    ids = f'{vendor:04x}:{product:04x}' if vendor is not None and product is not None else ''
    return '|'.join((name.strip(), ids, (serial or '').strip()))


def assign_keys(devices: list[TouchDevice]) -> list[TouchDevice]:
    """Set `key` on every device. Two devices with the same identity (two panels of the same model and no serial number)
    are told apart by the port they are plugged into, which survives reboots as long as the cable stays where it is."""
    base = {d.xid: identity_key(d.name, d.vendor, d.product, d.serial) for d in devices}
    counts: dict[str, int] = {}
    for key in base.values():
        counts[key] = counts.get(key, 0) + 1
    for d in devices:
        key = base[d.xid]
        if counts[key] > 1:
            key += '|@' + (d.phys or f'#{d.xid}')
        d.key = key
    return devices


def output_matrix(output: Output, screen_width: int, screen_height: int) -> tuple:
    """The Coordinate Transformation Matrix that maps a touch screen onto `output`, as 9 floats in row order.

    X multiplies the touch position, normalised to 0..1 over the whole X screen, by this matrix. So the matrix first
    rotates the unit square to follow the output's rotation (a panel mounted rotated reports its own native axes) and
    then scales and offsets it onto the output's rectangle inside the X screen."""
    if screen_width <= 0 or screen_height <= 0 or output.width <= 0 or output.height <= 0:
        return IDENTITY
    scale = (output.width / screen_width, 0.0, output.x / screen_width,
             0.0, output.height / screen_height, output.y / screen_height,
             0.0, 0.0, 1.0)
    return _multiply(scale, _ROTATION_MATRICES.get(output.rotation & 0xF, IDENTITY))


def _multiply(a: tuple, b: tuple) -> tuple:
    return tuple(sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3)) for r in range(3) for c in range(3))


def plan(devices: list[TouchDevice], mapping: dict[str, str], outputs: list[Output], screen_size: tuple[int, int],
         managed: frozenset[str] | set[str] = frozenset()) -> dict[int, tuple]:
    """Matrix to write per X device id. A device mapped to an output that is not there (unplugged, or off) gets the
    identity, which is what it would have without any mapping. A device with no mapping is left alone (somebody else,
    such as an xorg.conf rule, may have calibrated it) unless it is in `managed`, i.e. this module mapped it earlier and
    the mapping has since been removed."""
    by_name = {o.name: o for o in outputs}
    result = {}
    for d in devices:
        wanted = mapping.get(d.key)
        if wanted is None and d.key not in managed:
            continue
        output = by_name.get(wanted or '')
        result[d.xid] = output_matrix(output, *screen_size) if output else IDENTITY
    return result


def _float_to_bits(value: float) -> int:
    return struct.unpack('=I', struct.pack('=f', value))[0]


def _bits_to_float(bits: int) -> float:
    return struct.unpack('=f', struct.pack('=I', bits & 0xFFFFFFFF))[0]


def matrix_to_property(matrix: tuple) -> list[int]:
    """9 floats as the 32-bit words of an XI2 FLOAT property."""
    return [_float_to_bits(v) for v in matrix]


def matrix_from_property(words) -> tuple:
    return tuple(_bits_to_float(w) for w in words)


def close_enough(a: tuple, b: tuple) -> bool:
    return len(a) == len(b) == 9 and all(abs(x - y) < 1e-4 for x, y in zip(a, b))


_EVENT_RE = re.compile(r'^/dev/input/(event\d+)$')


def sysfs_identity(device_node: Optional[str], root: str = '/sys/class/input') -> dict:
    """Serial and port of an input device, from sysfs. The X 'Device Product ID' property has no serial, and a touch
    screen that has one has it on its USB device, a few directories above the input node."""
    m = _EVENT_RE.match(device_node or '')
    if not m:
        return {}
    try:
        node = os.path.realpath(os.path.join(root, m[1], 'device'))
    except OSError:
        return {}
    found = {}
    phys = _read(os.path.join(node, 'phys'))
    if phys:
        found['phys'] = phys
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


# ── X ────────────────────────────────────────────────────────────────────────

class XTouch:
    """One connection to the X server, used for queries, for writing the matrices and for listening to events.
    Not thread-safe: it is created and used on the event loop's thread only."""

    def __init__(self, display=None):
        from Xlib import display as xdisplay
        from Xlib.ext import randr, xinput
        self._randr, self._xinput = randr, xinput
        self.d = display or xdisplay.Display()
        self.root = self.d.screen().root
        if not self.d.has_extension('XInputExtension'):
            raise RuntimeError('the X server has no XInput extension')
        self.xi_major = self.d.display.get_extension_major(xinput.extname)
        self.d.xinput_query_version()
        self.has_randr = self.d.has_extension('RANDR')
        self._atoms: dict[str, int] = {}

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass

    def fileno(self) -> int:
        return self.d.fileno()

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def select_events(self):
        """Ask for the events that mean 'the picture changed': a device was added or removed (XI2 hierarchy) and the
        screen layout or an output's rotation changed (RandR)."""
        xi = self._xinput
        self.root.xinput_select_events([(xi.AllDevices, xi.HierarchyChangedMask)])
        if self.has_randr:
            r = self._randr
            self.root.xrandr_select_input(r.RRScreenChangeNotifyMask | r.RRCrtcChangeNotifyMask
                                          | r.RROutputChangeNotifyMask)
        self.d.flush()

    def relevant_events(self) -> bool:
        """Drain the event queue; True when at least one event says the devices or the layout changed."""
        changed = False
        while self.d.pending_events():
            ev = self.d.next_event()
            name = type(ev).__name__
            if name in ('ScreenChangeNotify', 'CrtcChangeNotify', 'OutputChangeNotify'):
                changed = True
            elif getattr(ev, 'extension', None) == self.xi_major and getattr(ev, 'evtype', None) == \
                    self._xinput.HierarchyChanged:
                changed = True
        return changed

    def screen_size(self) -> tuple[int, int]:
        geometry = self.root.get_geometry()     # not Display.screen(): that is a copy from connection time
        return geometry.width, geometry.height

    def outputs(self) -> list[Output]:
        """Connected outputs that are being driven, with the rectangle they cover in the X screen."""
        if not self.has_randr:
            return []
        found = []
        resources = self.root.xrandr_get_screen_resources()
        for oid in resources.outputs:
            info = self.d.xrandr_get_output_info(oid, resources.config_timestamp)
            if info.connection != 0 or not info.crtc:           # 0 = RR_Connected
                continue
            crtc = self.d.xrandr_get_crtc_info(info.crtc, resources.config_timestamp)
            if crtc.width and crtc.height:
                name = info.name if isinstance(info.name, str) else info.name.decode()
                found.append(Output(name, crtc.x, crtc.y, crtc.width, crtc.height, crtc.rotation))
        return sorted(found, key=lambda o: o.name)

    def touch_devices(self) -> list[TouchDevice]:
        xi = self._xinput
        devices = []
        for info in self.d.xinput_query_device(xi.AllDevices).devices:
            if info.use != SLAVE_POINTER or not any(
                    c.type == xi.TouchClass and c.mode == DIRECT_TOUCH for c in info.classes):
                continue
            name = info.name if isinstance(info.name, str) else info.name.decode(errors='replace')
            device = TouchDevice(xid=info.deviceid, name=name)
            self._fill_identity(device)
            devices.append(device)
        return assign_keys(devices)

    def _property(self, xid: int, name: str, type_atom: int = 0, length: int = 16):
        reply = self.d.xinput_get_device_property(xid, self.atom(name), type_atom, 0, length)
        if not reply.value or not reply.value[1]:
            return None
        return reply.value[1]

    def _fill_identity(self, device: TouchDevice):
        try:
            ids = self._property(device.xid, 'Device Product ID')
            if ids is not None and len(ids) >= 2:
                device.vendor, device.product = int(ids[0]), int(ids[1])
            node = self._property(device.xid, 'Device Node')
            if node is not None:
                node = node.decode(errors='replace') if isinstance(node, (bytes, bytearray)) else str(node)
                for k, v in sysfs_identity(node.rstrip('\0')).items():
                    setattr(device, k, v)
            matrix = self._property(device.xid, MATRIX_PROPERTY, self.atom('FLOAT'), 9)
            if matrix is not None and len(matrix) == 9:
                device.matrix = matrix_from_property(matrix)
        except Exception as exc:        # a device that vanished in between, or an X that lacks a property
            logger.debug('Cannot read the identity of touch device {}: {}', device.xid, exc)

    def set_matrix(self, xid: int, matrix: tuple):
        self.d.xinput_change_device_property(xid, self.atom(MATRIX_PROPERTY), self.atom('FLOAT'), 0,
                                             (32, matrix_to_property(matrix)))
        self.d.sync()


def read_state(x: XTouch) -> tuple[list[TouchDevice], list[Output], tuple[int, int]]:
    return x.touch_devices(), x.outputs(), x.screen_size()


# Identities this process has written a matrix for, so that removing a mapping puts the device back to the identity
_managed: set[str] = set()


def apply_mapping(x: XTouch, mapping: dict[str, str]) -> tuple[list[TouchDevice], list[Output]]:
    """Write the matrix of every touch device (only where it differs from what is already set) and return what was
    found, with each device's matrix as it now is."""
    devices, outputs, size = read_state(x)
    for xid, matrix in plan(devices, mapping, outputs, size, _managed).items():
        device = next(d for d in devices if d.xid == xid)
        if device.key in mapping:
            _managed.add(device.key)
        if close_enough(device.matrix, matrix):
            continue
        try:
            x.set_matrix(xid, matrix)
            device.matrix = matrix
            logger.info('Touch device {!r} ({}) mapped to {}', device.name, device.xid,
                        mapping.get(device.key) or 'the whole screen')
        except Exception as exc:
            logger.warning('Cannot map touch device {!r}: {}', device.name, exc)
    return devices, outputs
