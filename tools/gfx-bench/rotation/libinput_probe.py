#!/usr/bin/env python3
"""
Runs ON the board: opens ONE input node with libinput itself (path context, no compositor) and says what libinput makes of it, then optionally prints
the transformed position of every touch for each calibration matrix in turn, so what libinput does with a real panel is read without labwc in the loop.

    libinput_probe.py /dev/input/event4 [SECONDS]      # capabilities, calibration support
    libinput_probe.py /dev/input/event4 SECONDS taps   # also: positions (0..1) of the touches that arrive, with the identity matrix and with a 90-degree one
"""
import ctypes
import os
import sys
import time

L = ctypes.CDLL('libinput.so.10')
CAPS = {0: 'keyboard', 1: 'pointer', 2: 'touch', 3: 'tablet_tool', 4: 'tablet_pad', 5: 'gesture', 6: 'switch'}
TOUCH_DOWN, TOUCH_MOTION, POINTER_MOTION_ABSOLUTE = 500, 502, 402

OPEN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
CLOSE = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)


class Interface(ctypes.Structure):
    _fields_ = [('open_restricted', OPEN), ('close_restricted', CLOSE)]


def open_restricted(path, flags, _data):
    return os.open(path.decode(), flags)


def close_restricted(fd, _data):
    os.close(fd)


iface = Interface(OPEN(open_restricted), CLOSE(close_restricted))
for name, restype, argtypes in (
        ('libinput_path_create_context', ctypes.c_void_p, [ctypes.POINTER(Interface), ctypes.c_void_p]),
        ('libinput_path_add_device', ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p]),
        ('libinput_device_has_capability', ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
        ('libinput_device_get_name', ctypes.c_char_p, [ctypes.c_void_p]),
        ('libinput_device_config_calibration_has_matrix', ctypes.c_int, [ctypes.c_void_p]),
        ('libinput_device_config_calibration_set_matrix', ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)]),
        ('libinput_device_get_size', ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]),
        ('libinput_dispatch', ctypes.c_int, [ctypes.c_void_p]),
        ('libinput_get_event', ctypes.c_void_p, [ctypes.c_void_p]),
        ('libinput_event_get_type', ctypes.c_int, [ctypes.c_void_p]),
        ('libinput_event_destroy', None, [ctypes.c_void_p]),
        ('libinput_event_get_touch_event', ctypes.c_void_p, [ctypes.c_void_p]),
        ('libinput_event_touch_get_x_transformed', ctypes.c_double, [ctypes.c_void_p, ctypes.c_uint32]),
        ('libinput_event_touch_get_y_transformed', ctypes.c_double, [ctypes.c_void_p, ctypes.c_uint32]),
        ('libinput_get_fd', ctypes.c_int, [ctypes.c_void_p])):
    fn = getattr(L, name)
    fn.restype, fn.argtypes = restype, argtypes

path, seconds = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0
ctx = L.libinput_path_create_context(ctypes.byref(iface), None)
dev = L.libinput_path_add_device(ctx, path.encode())
if not dev:
    sys.exit(f'libinput did not accept {path}')
print('name:', L.libinput_device_get_name(dev).decode())
print('capabilities:', [CAPS[c] for c in CAPS if L.libinput_device_has_capability(dev, c)])
print('calibration matrix supported:', bool(L.libinput_device_config_calibration_has_matrix(dev)))
w, h = ctypes.c_double(), ctypes.c_double()
print('size mm:', (w.value, h.value) if L.libinput_device_get_size(dev, ctypes.byref(w), ctypes.byref(h)) == 0 else 'unknown', w.value, h.value)
if len(sys.argv) > 3:
    matrices = {'identity': (1, 0, 0, 0, 1, 0), 'turn 90 (x\'=1-y, y\'=x)': (0, -1, 1, 1, 0, 0)}
    end = time.time() + seconds
    while time.time() < end:
        for name, m in matrices.items():
            arr = (ctypes.c_float * 6)(*m)
            L.libinput_device_config_calibration_set_matrix(dev, arr)
            L.libinput_dispatch(ctx)
            time.sleep(0.4)
            L.libinput_dispatch(ctx)
            while True:
                ev = L.libinput_get_event(ctx)
                if not ev:
                    break
                kind = L.libinput_event_get_type(ev)
                if kind == TOUCH_DOWN:
                    t = L.libinput_event_get_touch_event(ev)
                    print(f'{name:28s} touch down at ({L.libinput_event_touch_get_x_transformed(t, 1000) / 1000:.3f}, '
                          f'{L.libinput_event_touch_get_y_transformed(t, 1000) / 1000:.3f})', flush=True)
                L.libinput_event_destroy(ev)
