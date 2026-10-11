"""
Wake-on-LAN for monitors that have a network port of their own (large touch panels do): the magic packet is a
UDP broadcast, so it needs no tool and works whatever the OS of the sender. It only switches ON; a monitor that
supports WOL is switched off with another mechanism (DDC, serial, CEC).
"""
from __future__ import annotations

import asyncio
import re
import socket
import time
from typing import Optional

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


ETHERTYPE_WOL = 0x0842


def ethernet_frame(mac: str, source: bytes) -> bytes:
    """The magic packet as a raw Ethernet frame to the broadcast address with EtherType 0x0842: what RouterOS'
    `/tool wol` sends, and the form some devices answer to when they ignore the UDP one."""
    return b'\xff' * 6 + source + ETHERTYPE_WOL.to_bytes(2, 'big') + magic_packet(mac)


def _interfaces(interface: Optional[str]) -> list[str]:
    if interface:
        return [interface]
    return [name for _, name in socket.if_nameindex() if name != 'lo']


def _send_raw(mac: str, interface: Optional[str], repeat: int, spacing: float):
    """Needs root (AF_PACKET); without it only the UDP form goes out."""
    sockets = []
    for name in _interfaces(interface):
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
            sock.bind((name, 0))
            with open(f'/sys/class/net/{name}/address') as file:
                source = bytes.fromhex(file.read().strip().replace(':', ''))
            sockets.append((sock, ethernet_frame(mac, source)))
        except (OSError, ValueError):
            continue    # down, not an Ethernet interface, or no permission
    try:
        for _ in range(repeat):
            for sock, frame in sockets:
                try:
                    sock.send(frame)
                except OSError:
                    pass
            time.sleep(spacing)
    finally:
        for sock, _ in sockets:
            sock.close()


def _send_udp(mac: str, broadcast: str, port: int, repeat: int, spacing: float):
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(5)
        for _ in range(repeat):
            sock.sendto(packet, (broadcast, port))
            time.sleep(spacing)


async def wake(mac: str, broadcast: str = '255.255.255.255', port: int = 9, interface: Optional[str] = None,
               repeat: int = 10):
    """Send the magic packet both ways at once, `repeat` times a little apart: a panel that is waking drops the
    first ones. The raw Ethernet form goes out on `interface` (every interface when None)."""
    await asyncio.gather(
        asyncio.to_thread(_send_raw, mac, interface, repeat, 0.3),
        asyncio.to_thread(_send_udp, mac, broadcast, port, repeat, 0.3),
    )
