#!/usr/bin/env python3
"""
Runs ON the board: a virtual touch screen (uinput, DIRECT) that taps where it is told. Lines written to the FIFO
`/run/rot-touch.fifo` are `NX NY` (0..1 of the device, its own frame) and `quit`.
"""
import fcntl
import os
import struct
import time

UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT, UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = (
    0x40045564, 0x40045565, 0x40045567, 0x4004556e, 0x405c5503, 0x401c5504, 0x5501, 0x5502)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
FIFO = '/run/rot-touch.fifo'
MAX = 4095

fd = os.open('/dev/uinput', os.O_WRONLY)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
fcntl.ioctl(fd, UI_SET_KEYBIT, 0x14a)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
for axis in (0, 1, 0x2f, 0x35, 0x36, 0x39):
    fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
fcntl.ioctl(fd, UI_SET_PROPBIT, 1)          # INPUT_PROP_DIRECT: a touch screen
fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack('<HHHH80sI', 3, 0x1209, 0x7002, 1, b'UniTotem Test Touch', 0))
for axis, top in ((0, MAX), (1, MAX), (0x35, MAX), (0x36, MAX), (0x2f, 9), (0x39, 65535)):
    fcntl.ioctl(fd, UI_ABS_SETUP, struct.pack('<HHiiiiii', axis, 0, 0, 0, top, 0, 0, 0)[:28])
fcntl.ioctl(fd, UI_DEV_CREATE)


def event(kind, code, value):
    os.write(fd, struct.pack('<qqHHi', 0, 0, kind, code, value))


def tap(nx, ny):
    x, y = int(nx * MAX), int(ny * MAX)
    for code, value in ((0x2f, 0), (0x39, 1), (0x35, x), (0x36, y), (0, x), (1, y)):
        event(EV_ABS, code, value)
    event(EV_KEY, 0x14a, 1)
    event(EV_SYN, 0, 0)
    time.sleep(0.2)
    event(EV_ABS, 0x39, -1)
    event(EV_KEY, 0x14a, 0)
    event(EV_SYN, 0, 0)


if os.path.exists(FIFO):
    os.unlink(FIFO)
os.mkfifo(FIFO)
print('ready', flush=True)
while True:
    with open(FIFO) as pipe:
        for line in pipe:
            if line.strip() == 'quit':
                fcntl.ioctl(fd, UI_DEV_DESTROY)
                os.unlink(FIFO)
                raise SystemExit(0)
            nx, ny = map(float, line.split())
            tap(nx, ny)
