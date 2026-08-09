"""
Monitor power/input/volume control via CEC, DDC/CI and X (SCREEN-CONTROL-PLAN.md).

This package must stay importable on a headless node with no PySide6/cefpython
and no GUI at all - the WebSocket API needs to report "unavailable" for a
mechanism, not fail to import, on any node that lacks the hardware or the
tools. See manager/api/display.py's own note for the same constraint on the
existing Display API.

Only the pure, dependency-free parsing/decoding/resolution logic lives here so
far (ddc.py, identity.py, mechanism.py): text parsers for ddcutil's output, an
EDID decoder, an xrandr EDID-property extractor, an output-name normaliser and
mechanism-priority resolution. None of it shells out or touches a device.

The hardware layer - the subprocess calls, per-bus/per-device asyncio locks,
sysfs/xrandr output enumeration, and the async facade that wires all of this
together (probe(), apply_power/input/volume/mute()) - is SCREEN-CONTROL-PLAN.md
step 4, not yet implemented. This module intentionally does not import them
yet; nothing here shells out to ddcutil/cec-ctl/xrandr.
"""
