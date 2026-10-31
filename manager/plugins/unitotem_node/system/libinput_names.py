"""
The name libinput gives an input device, which is the one the compositor's configuration (`<touch deviceName>`, `<libinput><device category>`) matches.
It is not the kernel's: a USB panel that has two interfaces with the same name (`TSTP CTouch`) is `TSTP CTouch (USB 1-1.4)` for libinput, so a
configuration written with the kernel name matches nothing (the touch then spans the layout, unturned, and no calibration reaches it).
libinput is asked through its own library (ctypes), one node at a time, without the compositor and without grabbing the device.
"""
import ctypes
import os
from typing import Iterable, Optional

from loguru import logger

_OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_CLOSE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)


class _Interface(ctypes.Structure):
    _fields_ = [('open_restricted', _OPEN), ('close_restricted', _CLOSE)]


_lib = None
_iface = None


def _load():
    global _lib, _iface
    if _lib is not None:
        return _lib
    for soname in ('libinput.so.10', 'libinput.so'):
        try:
            lib = ctypes.CDLL(soname)
            break
        except OSError:
            lib = None
    if lib is None:
        _lib = False
        return _lib
    lib.libinput_path_create_context.restype = ctypes.c_void_p
    lib.libinput_path_create_context.argtypes = [ctypes.POINTER(_Interface), ctypes.c_void_p]
    lib.libinput_path_add_device.restype = ctypes.c_void_p
    lib.libinput_path_add_device.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.libinput_device_get_name.restype = ctypes.c_char_p
    lib.libinput_device_get_name.argtypes = [ctypes.c_void_p]
    lib.libinput_unref.restype = ctypes.c_void_p
    lib.libinput_unref.argtypes = [ctypes.c_void_p]
    _iface = _Interface(_OPEN(lambda path, flags, _d: os.open(path.decode(), flags)), _CLOSE(lambda fd, _d: os.close(fd)))
    _lib = lib
    return _lib


def name_of(node: str) -> Optional[str]:
    """libinput's name of the device behind `/dev/input/eventN`, None when libinput or the node is not there."""
    lib = _load()
    if not lib:
        return None
    try:
        context = lib.libinput_path_create_context(ctypes.byref(_iface), None)
        if not context:
            return None
        try:
            device = lib.libinput_path_add_device(context, node.encode())
            return lib.libinput_device_get_name(device).decode() if device else None
        finally:
            lib.libinput_unref(context)
    except Exception as exc:                   # a library that is there but different: the kernel name is the fallback
        logger.debug('libinput name of {}: {}', node, exc)
        return None


def names(nodes: Iterable[str]) -> dict[str, str]:
    return {node: found for node in nodes if (found := name_of(node))}
