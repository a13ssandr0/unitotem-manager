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
