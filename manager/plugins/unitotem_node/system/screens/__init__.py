"""
Monitor power/input/volume control via CEC, DDC/CI and X (SCREEN-CONTROL-PLAN.md).

This package must stay importable on a headless node with no PySide6/cefpython
and no GUI at all - the WebSocket API needs to report "unavailable" for a
mechanism, not fail to import, on any node that lacks the hardware or the
tools. See manager/api/display.py's own note for the same constraint on the
existing Display API.

The pure, dependency-free parsing/decoding/resolution logic lives in ddc.py, identity.py
and mechanism.py: text parsers for ddcutil's output, an EDID decoder, an output-name
normaliser and mechanism-priority resolution. None of it shells out or touches a device.

The hardware layer (subprocess calls, per-bus/per-device asyncio locks, output enumeration
and the async facade probe(), apply_power/input/volume/mute()) is in the other modules of
the package. This module intentionally imports none of them.
"""
