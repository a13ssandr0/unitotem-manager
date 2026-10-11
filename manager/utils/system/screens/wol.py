"""
Wake-on-LAN for monitors that have a network port of their own (large touch panels do): the magic packet is a
UDP broadcast, so it needs no tool and works whatever the OS of the sender. It only switches ON; a monitor that
supports WOL is switched off with another mechanism (DDC, serial, CEC).
"""
from __future__ import annotations

import asyncio
import re
import socket

_MAC_RE = re.compile(r'^([0-9a-fA-F]{2}[:-]?){5}[0-9a-fA-F]{2}$')


def normalise_mac(mac: str) -> str:
    """'aa-bb-cc-dd-ee-ff', 'AABBCCDDEEFF' and 'aa:bb:cc:dd:ee:ff' are all the same address."""
    if not _MAC_RE.match(mac.strip()):
        raise ValueError(f'{mac!r} is not a MAC address')
    digits = re.sub(r'[^0-9a-fA-F]', '', mac).lower()
    return ':'.join(digits[i:i + 2] for i in range(0, 12, 2))


def magic_packet(mac: str) -> bytes:
    """Six 0xFF bytes followed by the MAC address repeated sixteen times."""
    raw = bytes.fromhex(normalise_mac(mac).replace(':', ''))
    return b'\xff' * 6 + raw * 16


def _send(mac: str, broadcast: str, port: int):
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(5)
        # Three times, a little apart: a monitor that is just waking can drop the first one
        sock.sendto(packet, (broadcast, port))
        sock.sendto(packet, (broadcast, port))
        sock.sendto(packet, (broadcast, port))


async def wake(mac: str, broadcast: str = '255.255.255.255', port: int = 9):
    await asyncio.to_thread(_send, mac, broadcast, port)
