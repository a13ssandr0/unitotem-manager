"""Which address the node is reachable at, and whether it is connected to a network at all."""
import socket
from typing import Optional

import psutil

_RTF_GATEWAY = 0x2


def default_interface(route_file: str = '/proc/net/route') -> Optional[str]:
    """The interface that carries the default route, None when there is none (the node is not on any network)."""
    try:
        with open(route_file) as file:
            next(file, None)            # header
            for line in file:
                fields = line.split()
                if len(fields) > 3 and fields[1] == '00000000' and int(fields[3], 16) & _RTF_GATEWAY:
                    return fields[0]
    except OSError:
        pass
    return None


def default_ipv4() -> Optional[str]:
    """The IPv4 address on the default route's interface, None when the node is not connected."""
    interface = default_interface()
    if interface is None:
        return None
    for address in psutil.net_if_addrs().get(interface, []):
        if address.family == socket.AF_INET:
            return address.address
    return None


def all_ipv4() -> list[str]:
    """Every non-loopback, non-link-local IPv4 address of the node (the default route's own first)."""
    default = default_interface()
    found = []
    for interface, addresses in psutil.net_if_addrs().items():
        for address in addresses:
            if address.family == socket.AF_INET and not address.address.startswith(('127.', '169.254.')):
                found.append((interface != default, interface, address.address))
    return [address for _, _, address in sorted(found)]


def reachable_ipv4() -> Optional[str]:
    """The address the node is reached at: the default route's, else any other usable one, None when there is none."""
    return default_ipv4() or next(iter(all_ipv4()), None)


def address_text() -> str:
    """For a screen: the default route's address, else every address, else a plain statement. Never 'None'."""
    default = default_ipv4()
    others = [a for a in all_ipv4() if a != default]
    if default:
        return default
    return ', '.join(others) if others else 'no network yet'


async def address_changes():
    """Yield each time an IPv4 address or route changes (netlink, no polling); bursts are merged into one event."""
    import asyncio
    loop = asyncio.get_running_loop()
    RTMGRP_IPV4_IFADDR, RTMGRP_IPV4_ROUTE = 0x10, 0x40
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0)    # NETLINK_ROUTE
    sock.setblocking(False)
    sock.bind((0, RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE))
    ready = asyncio.Event()
    loop.add_reader(sock.fileno(), ready.set)
    try:
        while True:
            await ready.wait()
            await asyncio.sleep(0.5)            # DHCP adds the address and the route a moment apart
            ready.clear()
            try:
                while sock.recv(65536):
                    pass
            except BlockingIOError:
                pass
            yield
    finally:
        loop.remove_reader(sock.fileno())
        sock.close()
