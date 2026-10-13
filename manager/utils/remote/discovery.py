"""
Discovery of unpaired clients on the local network, over mDNS / DNS-SD (the python3-zeroconf library).

mDNS rather than a broadcast packet of our own: it is the standard way for a device to say "I am here", every OS and
tool (avahi-browse, dns-sd) understands it for debugging, it is multicast (no directed-broadcast or subnet-mask
guesswork), the library takes care of interfaces coming and going, and there is no protocol to design, to parse by hand
and to get wrong. An unpaired client registers a `_unitotem-client._tcp` service; servers browse for it.

What is announced is only a hint of WHERE to look: anyone on the LAN can announce anything. Nothing found here is
trusted; the identity of the device is the certificate it presents when the server contacts it (utils.remote.pairing),
which the user is asked to confirm. Whatever arrives is parsed strictly and bounded in size.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence

import psutil
from loguru import logger

try:
    from zeroconf import IPVersion, ServiceInfo, ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    AVAILABLE = True
except ImportError:         # the core installs without it: no discovery, manual pairing keeps working
    AVAILABLE = False

SERVICE_TYPE = '_unitotem-client._tcp.local.'
PROTOCOL_VERSION = '1'
_ID = re.compile(r'[0-9a-f]{32}')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
MAX_NAME = 64


@dataclass(frozen=True)
class Announcement:
    node_id: str
    name: str
    host: str               # where to contact it
    port: int               # its web port (pairing happens there)
    link_port: int


def _port(value, default: Optional[int] = None) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port < 65536 else default


def _text(value) -> Optional[str]:
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def usable_address(address: str) -> Optional[str]:
    """An IPv4 address of a peer on this network, or None for everything that is not one (loopback, multicast, junk)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version != 4 or ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return None
    return str(ip)


def parse_announcement(properties: Mapping, addresses: Sequence[str], port: Optional[int]) -> Optional[Announcement]:
    """The announcement of a service as seen on the network -> what a server shows, or None if it does not make sense.
    Never raises: the input is whatever any device on the LAN decided to send."""
    props = {_text(k): _text(v) for k, v in (properties or {}).items()}
    if props.get('v') != PROTOCOL_VERSION:
        return None
    node_id = props.get('id')
    if not node_id or not _ID.fullmatch(node_id):
        return None
    name = _CONTROL.sub('', props.get('name') or '').strip()[:MAX_NAME] or node_id[:8]
    host = next((a for a in map(usable_address, addresses or ()) if a), None)
    web_port = _port(props.get('web'), _port(port))
    if host is None or web_port is None:
        return None
    return Announcement(node_id, name, host, web_port, _port(props.get('link'), 4443))


def local_addresses() -> list[str]:
    """The IPv4 addresses this node can be reached at (what an announcement carries)."""
    out = []
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and usable_address(a.address):
                out.append(a.address)
    return out


class Announcer:
    """Makes this node visible as an unpaired client for as long as it is one."""

    def __init__(self):
        self._zc = None
        self._info = None

    @property
    def active(self) -> bool:
        return self._info is not None

    async def start(self, node_id: str, name: str, web_port: int, link_port: int):
        if not AVAILABLE or self._info is not None:
            return
        addresses = [socket.inet_aton(a) for a in local_addresses()]
        if not addresses:
            logger.warning('No network address to announce this client at')
            return
        self._info = ServiceInfo(
            SERVICE_TYPE, f'unitotem-{node_id[:12]}.{SERVICE_TYPE}', addresses=addresses, port=web_port,
            properties={'v': PROTOCOL_VERSION, 'id': node_id, 'name': name[:MAX_NAME], 'web': str(web_port),
                        'link': str(link_port)},
            server=f'unitotem-{node_id[:12]}.local.')
        try:
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zc.async_register_service(self._info)
            logger.info('Announcing this node as an unpaired client')
        except Exception as e:
            logger.error('Cannot announce this client on the network: {}', e)
            await self.stop()

    async def stop(self):
        zc, info, self._zc, self._info = self._zc, self._info, None, None
        if zc is not None:
            try:
                if info is not None:
                    await zc.async_unregister_service(info)
            finally:
                await zc.async_close()


class Browser:
    """Keeps the list of the unpaired clients currently announcing themselves."""

    def __init__(self, changed: Callable[[], None]):
        self.changed = changed
        self.found: dict[str, Announcement] = {}
        self._by_name: dict[str, str] = {}
        self._zc = None
        self._browser = None

    @property
    def active(self) -> bool:
        return self._browser is not None

    async def start(self):
        if not AVAILABLE or self._browser is not None:
            return
        try:
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            self._browser = AsyncServiceBrowser(self._zc.zeroconf, SERVICE_TYPE, handlers=[self._on_change])
        except Exception as e:
            logger.error('Cannot look for clients on the network: {}', e)
            await self.stop()

    async def stop(self):
        browser, zc, self._browser, self._zc = self._browser, self._zc, None, None
        self.found.clear()
        self._by_name.clear()
        if browser is not None:
            await browser.async_cancel()
        if zc is not None:
            await zc.async_close()

    def _on_change(self, zeroconf, service_type, name, state_change):
        # called by the library in its own thread of control: hand over to the loop
        if state_change is ServiceStateChange.Removed:
            node_id = self._by_name.pop(name, None)
            if node_id and self.found.pop(node_id, None):
                self.changed()
        else:
            asyncio.ensure_future(self._resolve(zeroconf, service_type, name))

    async def _resolve(self, zeroconf, service_type: str, name: str):
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zeroconf, 3000):
            return
        found = parse_announcement(info.properties, info.parsed_addresses(), info.port)
        if found is None:
            logger.debug('Ignoring an announcement that makes no sense: {}', name)
            return
        if len(self.found) >= 64 and found.node_id not in self.found:
            return          # a device announcing thousands of names must not fill the list (or the memory)
        self._by_name[name] = found.node_id
        if self.found.get(found.node_id) != found:
            self.found[found.node_id] = found
            self.changed()
