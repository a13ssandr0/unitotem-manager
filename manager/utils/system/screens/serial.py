"""
Monitors with an RS-232 (or USB-serial) control port, driven with raw bytes. Every vendor has its own command set,
so nothing is built in: the commands are configured per output as text with \\r \\n \\xNN escapes, taken from the
monitor's manual.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import re
import termios

_BAUDS = {rate: getattr(termios, f'B{rate}') for rate in
          (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200) if hasattr(termios, f'B{rate}')}

_ESCAPES = {'r': b'\r', 'n': b'\n', 't': b'\t', '\\': b'\\'}
_TOKEN_RE = re.compile(r'\\(x[0-9a-fA-F]{2}|[rnt\\])')


def parse_command(text: str) -> bytes:
    """Turn the configured text into bytes: '\\r', '\\n', '\\t', '\\\\' and '\\xNN' are escapes, the rest is ASCII."""
    out = bytearray()
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        out += text[pos:m.start()].encode('ascii')
        token = m.group(1)
        out += bytes([int(token[1:], 16)]) if token[0] == 'x' else _ESCAPES[token]
        pos = m.end()
    out += text[pos:].encode('ascii')
    return bytes(out)


def valid_port(port: str) -> bool:
    """Only real serial device nodes: the path ends up in open(), so nothing else is accepted."""
    return bool(re.fullmatch(r'/dev/(ttyS|ttyUSB|ttyACM|ttyAMA|serial/by-id/)[\w.:-]*\d*[\w.:-]*', port))


def _send(port: str, baud: int, payload: bytes, reply_wait: float) -> bytes:
    if not valid_port(port):
        raise ValueError(f'{port!r} is not a serial port')
    if baud not in _BAUDS:
        raise ValueError(f'unsupported baud rate {baud}')
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # another process (or another call) is talking to it
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0                                                   # iflag: raw
        attrs[1] = 0                                                   # oflag: raw
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL        # 8N1, no flow control
        attrs[3] = 0                                                   # lflag: raw
        attrs[4] = attrs[5] = _BAUDS[baud]
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        os.write(fd, payload)
        termios.tcdrain(fd)
        import select
        reply = b''
        if reply_wait:
            ready, _, _ = select.select([fd], [], [], reply_wait)
            if ready:
                reply = os.read(fd, 256)
        return reply
    finally:
        os.close(fd)


async def send(port: str, baud: int, command: str, reply_wait: float = 0.5) -> bytes:
    """Send one configured command and return whatever the monitor answers within `reply_wait` seconds."""
    return await asyncio.to_thread(_send, port, baud, parse_command(command), reply_wait)
