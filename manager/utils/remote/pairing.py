"""
The pieces of pairing that do not depend on a running node: limits on what strangers can ask, the list of requests
waiting for the administrator's approval, the validation of what a peer sends, and the HTTP calls between two nodes.

How pairing works (the orchestration is in utils.remote.service):

* A node that is an unpaired client ACCEPTS adoption from whoever asks, like a Ubiquiti device: nothing in a
  factory-fresh client can tell a legitimate server from another. This is not blind trust, though: the administrator of
  the server is shown the certificate fingerprint the device presents, must confirm it, and the same fingerprint is on the
  client's own page for comparison, which is what defeats somebody standing between the two. The client, for its part,
  shows who adopted it and can leave at any time.
* A client that wants to join a server asks for it (a "pairing request"); the server's administrator approves it, and the
  approval is an adoption started from the server: nothing is ever pinned because a request arrived.
* A node already paired is never adopted by a request alone.
* Everything is rate limited and bounded: a hostile device on the LAN can annoy but not fill memory or the page.
"""
from __future__ import annotations

import ipaddress
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import aiohttp

from utils.remote import identity as ident

MAX_BODY = 16 * 1024
MAX_CERT_PEM = 8 * 1024
PROTOCOL_VERSION = 1


class PairingError(Exception):
    """Something to tell the user as it is: the message is for the page"""


class RateLimiter:
    """At most `limit` events per `window` seconds for each key (an address, normally)."""

    def __init__(self, limit: int, window: float, now: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._now = limit, window, now
        self._events: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._now()
        events = self._events[key]
        while events and events[0] <= now - self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        if len(self._events) > 1024:        # keys of the past are not worth keeping
            for k in [k for k, v in self._events.items() if not v or v[-1] <= now - self.window]:
                del self._events[k]
        return True


@dataclass
class PairRequest:
    """A client asking to be adopted, waiting for the administrator"""
    node_id: str
    name: str
    ip: str
    web_port: int
    link_port: int
    cert: str
    at: float = field(default_factory=time.time)

    @property
    def fingerprint(self) -> str:
        return ident.fingerprint(self.cert)


class PairRequests:
    """The pending requests: few, short-lived, and never more than a few from the same address."""

    def __init__(self, max_total: int = 16, max_per_ip: int = 3, ttl: float = 600, now: Callable[[], float] = time.time):
        self.max_total, self.max_per_ip, self.ttl, self._now = max_total, max_per_ip, ttl, now
        self._items: dict[str, PairRequest] = {}

    def _expire(self):
        for key in [k for k, r in self._items.items() if r.at < self._now() - self.ttl]:
            del self._items[key]

    def add(self, request: PairRequest):
        self._expire()
        # the same node asking again replaces its request (it may have a new address); that is not a new slot
        others = [r for r in self._items.values() if r.node_id != request.node_id]
        if len(others) >= self.max_total:
            raise PairingError('Too many pairing requests are waiting')
        if sum(1 for r in others if r.ip == request.ip) >= self.max_per_ip:
            raise PairingError('Too many pairing requests from this address')
        request.at = self._now()
        self._items[request.node_id] = request

    def get(self, node_id: str) -> Optional[PairRequest]:
        self._expire()
        return self._items.get(node_id)

    def remove(self, node_id: str) -> Optional[PairRequest]:
        return self._items.pop(node_id, None)

    def all(self) -> list[PairRequest]:
        self._expire()
        return list(self._items.values())

    def clear(self):
        self._items.clear()


def clean_name(value, default: str = 'unnamed') -> str:
    text = value if isinstance(value, str) else ''
    return ''.join(c for c in text if c.isprintable()).strip()[:64] or default


def clean_port(value, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port < 65536 else default


def clean_cert(value) -> str:
    """The certificate a peer sent, as canonical PEM. Raises PairingError for anything that is not exactly one."""
    if not isinstance(value, str) or len(value) > MAX_CERT_PEM:
        raise PairingError('The device did not present a valid certificate')
    try:
        return ident.der_to_pem(ident.cert_der(value))
    except ValueError:
        raise PairingError('The device did not present a valid certificate') from None


def clean_host(value) -> str:
    """The IPv4 address typed by the user (or taken from where a request came from)."""
    try:
        ip = ipaddress.IPv4Address(str(value).strip())
    except ValueError:
        raise PairingError('Not a valid IPv4 address') from None
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified:
        raise PairingError('Not an address of another device')
    return str(ip)


def parse_hello(data) -> dict:
    """The answer of GET /remote/hello of another node, validated."""
    if not isinstance(data, dict) or data.get('v') != PROTOCOL_VERSION:
        raise PairingError('The device does not speak this version of the pairing protocol (is it a UniTotem node?)')
    cert = clean_cert(data.get('cert'))
    return {
        'cert': cert, 'fingerprint': ident.fingerprint(cert), 'node_id': ident.node_id(cert),
        'name': clean_name(data.get('name')),
        'role': 'client' if data.get('role') == 'client' else 'server',
        'adoptable': data.get('adoptable') is True,
        'web_port': clean_port(data.get('web_port'), 443), 'link_port': clean_port(data.get('link_port'), 4443),
    }


async def call(host: str, port: int, path: str, body: Optional[dict] = None) -> dict:
    """GET (no body) or POST (JSON) to another node. Its web certificate is not checked: that is the certificate of a
    device nobody has paired yet and there is nothing to check it against. What identifies the node is the certificate in
    the answer, which the user confirms by its fingerprint, and from then on the pinned link."""
    url = f'https://{host}:{port}{path}'
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.request('POST' if body is not None else 'GET', url, json=body, ssl=False) as response:
                raw = await response.content.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise PairingError('The answer of the device is too big')
                if response.status == 429:
                    raise PairingError('The device is receiving too many requests, try again in a minute')
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = {}
                if response.status != 200:
                    detail = data.get('detail') if isinstance(data, dict) else None
                    raise PairingError(detail if isinstance(detail, str) and len(detail) < 200
                                       else f'The device answered HTTP {response.status}')
                return data
    except PairingError:
        raise
    except (aiohttp.ClientError, OSError, TimeoutError) as e:
        raise PairingError(f'Cannot reach {host}:{port} ({e.__class__.__name__})') from None
