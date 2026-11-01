"""
A GPIO line that powers a monitor (a relay in its supply) or presses its power button, through libgpiod (python3-libgpiod, API 2).

Two modes, chosen per output:
* level: the line is held asserted while the screen is on and released from assertion to switch it off. The process owns the line while it
  runs; when it ends the line goes back to what the hardware makes of it (a pull resistor, a device tree `gpio-hog`), which only the installer knows:
  so on shutdown the line is asserted ("screen on") before it is given back.
* pulse: the line is asserted for `pulse_ms` and released, once for on and once for off (a monitor whose button is the only control).

A monitor behind either one keeps no link the compositor can see, so it is one of the "blind" mechanisms of control.py.
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from typing import Any

from loguru import logger

CONSUMER = 'unitotem'
_CHIP_RE = re.compile(r'(?:/dev/)?(gpiochip\d{1,3})')


class GpioError(Exception):
    """The line cannot be driven; the text says why."""


def normalise_chip(value: str) -> str:
    """`gpiochip0` or `/dev/gpiochip0` -> `gpiochip0`; nothing else becomes part of a path."""
    m = _CHIP_RE.fullmatch(value)
    if not m:
        raise ValueError(f'{value!r} is not a GPIO chip (gpiochipN)')
    return m.group(1)


def _lib():
    try:
        import gpiod
        from gpiod.line import Direction, Value
    except ImportError as exc:
        raise GpioError('the GPIO library is not installed (package python3-libgpiod, API 2)') from exc
    return gpiod, Direction, Value


_lock = threading.Lock()
# (chip, line, active_low) -> the request that holds the line while a level-mode screen is controlled
_held: dict[tuple[str, int, bool], Any] = {}


def _path(chip: str) -> str:
    return f'/dev/{normalise_chip(chip)}'


def _open(gpiod, Direction, Value, chip: str, line: int, active_low: bool, asserted: bool):
    try:
        return gpiod.request_lines(
            _path(chip), consumer=CONSUMER,
            config={line: gpiod.LineSettings(direction=Direction.OUTPUT, active_low=active_low,
                                             output_value=Value.ACTIVE if asserted else Value.INACTIVE)})
    except (OSError, ValueError) as exc:
        raise GpioError(f'cannot use line {line} of {chip}: {exc}') from exc


def _set_level(chip: str, line: int, active_low: bool, asserted: bool):
    gpiod, Direction, Value = _lib()
    key = (normalise_chip(chip), line, active_low)
    with _lock:
        request = _held.get(key)
        if request is None:
            # the line is requested at the value wanted, so it never glitches to the other one on the way
            _held[key] = _open(gpiod, Direction, Value, *key, asserted)
            return
        try:
            request.set_value(line, Value.ACTIVE if asserted else Value.INACTIVE)
        except OSError as exc:
            _held.pop(key, None)
            raise GpioError(f'cannot drive line {line} of {chip}: {exc}') from exc


def _pulse(chip: str, line: int, active_low: bool, milliseconds: int):
    gpiod, Direction, Value = _lib()
    with _lock:
        if any(k[:2] == (normalise_chip(chip), line) for k in _held):
            raise GpioError(f'line {line} of {chip} is held by a level-mode screen')
        request = _open(gpiod, Direction, Value, chip, line, active_low, True)
        try:
            time.sleep(milliseconds / 1000)
            request.set_value(line, Value.INACTIVE)
        finally:
            request.release()


async def set_level(chip: str, line: int, active_low: bool, on: bool):
    await asyncio.to_thread(_set_level, chip, line, active_low, on)


async def pulse(chip: str, line: int, active_low: bool, milliseconds: int):
    await asyncio.to_thread(_pulse, chip, line, active_low, milliseconds)


def availability(cfg) -> tuple[bool, str]:
    """(usable, why not) for an output's GPIO settings (None when nothing is configured)."""
    if cfg is None:
        return False, 'no GPIO line configured'
    try:
        _lib()
    except GpioError as exc:
        return False, str(exc)
    if not os.path.exists(_path(cfg.chip)):
        return False, f'{cfg.chip} does not exist on this machine'
    return True, ''


def release_all():
    """At shutdown: assert every held line (screens on) and give it back."""
    if not _held:
        return
    try:
        _, _, Value = _lib()
    except GpioError:
        return
    with _lock:
        for key, request in list(_held.items()):
            try:
                request.set_value(key[1], Value.ACTIVE)
            except OSError as exc:
                logger.warning('GPIO line {} of {} not set to on at shutdown: {}', key[1], key[0], exc)
            try:
                request.release()
            except OSError:
                pass
        _held.clear()


def chips() -> list[dict]:
    """The GPIO chips and their lines, for the web UI to offer: [{name, label, lines: [{offset, name, used_by}]}]."""
    try:
        gpiod, _, _ = _lib()
    except GpioError as exc:
        return [{'name': '', 'label': str(exc), 'lines': [], 'error': True}]
    import glob
    out = []
    for path in sorted(glob.glob('/dev/gpiochip*')):
        if not _CHIP_RE.fullmatch(path):
            continue
        try:
            with gpiod.Chip(path) as chip:
                info = chip.get_info()
                lines = []
                for offset in range(info.num_lines):
                    line = chip.get_line_info(offset)
                    lines.append({'offset': offset, 'name': line.name or '', 'used_by': line.consumer if line.used else ''})
                out.append({'name': normalise_chip(path), 'label': info.label, 'lines': lines})
        except OSError as exc:
            logger.warning('Cannot read GPIO chip {}: {}', path, exc)
    return out
