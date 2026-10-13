"""
What the machine is: the model of the computer, of its motherboard or of its single-board computer, and the name of its
processor. Static facts, read from where the kernel and the firmware publish them (the same places the pcinfotree
tools read them): the device tree for an SBC, the SMBIOS/DMI strings for a PC, /proc/cpuinfo for the processor.
Nothing here runs a command, and every source may be missing (a virtual machine, a container, another architecture).
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Optional

DMI = Path('/sys/class/dmi/id')
DEVICE_TREE_MODEL = Path('/proc/device-tree/model')
CPUINFO = Path('/proc/cpuinfo')

# What firmware leaves in a field nobody filled in (board makers' placeholders): it names nothing
_PLACEHOLDERS = {
    '', 'to be filled by o.e.m.', 'to be filled by o.e.m', 'system product name', 'system version', 'default string',
    'default', 'not specified', 'not applicable', 'n/a', 'none', 'unknown', 'o.e.m.', 'oem', 'type1productconfigid',
    'system manufacturer', 'base board product name', 'base board manufacturer', 'x.x', 'invalid', '0',
}


def _read(path: Path) -> str:
    try:
        return path.read_text(errors='replace').replace('\x00', '').strip()
    except OSError:
        return ''


def _meaningful(value: str) -> str:
    return '' if value.strip().lower() in _PLACEHOLDERS else ' '.join(value.split())


def _join(vendor: str, name: str) -> str:
    """'Dell Inc.' + 'OptiPlex 7010', without saying the vendor twice when the name already starts with it."""
    if not name:
        return vendor
    first = vendor.split()[0].lower().rstrip('.,') if vendor else ''
    return name if not vendor or name.lower().startswith(first) else f'{vendor} {name}'


def machine_model(dmi: Path = DMI, device_tree: Path = DEVICE_TREE_MODEL) -> Optional[str]:
    """'Raspberry Pi 4 Model B Rev 1.4' on an SBC, the PC's product name when the maker gave one ('Dell Inc. OptiPlex
    7010'), otherwise its motherboard ('ASUSTeK COMPUTER INC. ROG STRIX X299-E GAMING II'); None when nothing says."""
    model = _meaningful(_read(device_tree))
    if model:
        return model
    product = _meaningful(_read(dmi / 'product_name'))
    if product:
        return _join(_meaningful(_read(dmi / 'sys_vendor')), product)
    board = _meaningful(_read(dmi / 'board_name'))
    if board:
        return _join(_meaningful(_read(dmi / 'board_vendor')), board)
    return None


def cpu_name(cpuinfo: Path = CPUINFO) -> Optional[str]:
    """The processor's model name; 'N x name' when there are several sockets, 'a + b' when they differ."""
    models: list[str] = []
    sockets: set[str] = set()
    for line in _read(cpuinfo).splitlines():
        key, _, value = line.partition(':')
        key, value = key.strip().lower(), ' '.join(value.split())
        if key == 'model name' and value and value not in models:
            models.append(value)
        elif key == 'physical id':
            sockets.add(value)
    if not models:
        return None
    name = re.sub(r'\s*\((?:R|TM)\)', '', ' + '.join(models))      # "Intel(R) Core(TM) i7" -> "Intel Core i7"
    return f'{len(sockets)} x {name}' if len(sockets) > 1 else name


@cache
def machine_info() -> dict:
    return {'machine': machine_model(), 'cpu_name': cpu_name()}
