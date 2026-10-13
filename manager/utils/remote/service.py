"""
The running side of remote control: owns the listener (server role) or the uplink (client role) of this node, the
discovery of clients, the pairing between nodes, and tells the web UI whenever something it shows changes.
One per process: `service`.
"""
from __future__ import annotations

import asyncio
import time
from socket import gethostname
from typing import Optional

from loguru import logger

from api.ws.endpoints import REMOTE_WS, WS
from utils.models.command_line import cmdargs
from utils.models.remote import RemoteManager
from utils.remote import identity as ident
from utils.remote import pairing
from utils.remote.discovery import AVAILABLE as DISCOVERY_AVAILABLE, Announcer, Browser
from utils.remote.link import ClientUplink, LinkServer
from utils.remote.pairing import PairingError, PairRequest, PairRequests, RateLimiter

PENDING_SERVER_TTL = 600


def _plain_fingerprint(value: str) -> str:
    """What the page sends back (the colon-separated form it showed) -> the form fingerprints are compared in"""
    return (value or '').replace(':', '').strip().lower()


class RemoteService:
    def __init__(self):
        self.link = LinkServer(REMOTE_WS, self.changed)
        self.uplink = ClientUplink(self._relay, self.changed)
        self.announcer = Announcer()
        self.browser = Browser(self.changed)
        self.requests = PairRequests()
        # a client that asked a server to adopt it: only that server may, for a while (see accept_adoption)
        self.pending_server: Optional[dict] = None
        # what strangers can ask of this node, per address, per minute
        self.limit_public = RateLimiter(30, 60)
        self.limit_adopt = RateLimiter(6, 60)
        self.limit_request = RateLimiter(6, 60)
        self._push_task: Optional[asyncio.Task] = None

    @property
    def rm(self) -> RemoteManager:
        return RemoteManager.get_instance()

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self):
        """Bring up whatever the saved role needs. Never raises: remote control failing must not take the node with it."""
        try:
            self.rm.ensure_identity()
            await self.apply_role()
        except Exception:
            logger.exception('Remote control did not start, the node keeps running on its own')

    async def apply_role(self):
        """(Re)start the right half for the current role, after the role or the set of paired nodes changed."""
        rm = self.rm
        if rm.role == 'server':
            self.pending_server = None
            await self.announcer.stop()
            await self.uplink.stop()
            await self.link.reload()
            await self.browser.start()
        else:
            await self.browser.stop()
            self.requests.clear()
            await self.link.stop()
            if rm.is_paired_client:
                self.pending_server = None
                await self.announcer.stop()
                self.uplink.start()
            else:
                await self.uplink.stop()
                await self.announcer.start(rm.node_id, gethostname(), cmdargs.port_secure, cmdargs.port_link)
        self.changed()

    async def stop(self):
        await self.uplink.stop()
        await self.link.stop()
        await self.announcer.stop()
        await self.browser.stop()

    # ── towards the viewer of this node ───────────────────────────────────

    @staticmethod
    async def _relay(target: str, data: dict):
        # nocache: the server resends what a window needs when the window (re)registers; a copy kept here would be stale
        await REMOTE_WS.broadcast(target, nocache=True, **data)

    # ── what this node says about itself to anybody (GET /remote/hello) ───

    def hello(self) -> dict:
        rm = self.rm
        return {'v': pairing.PROTOCOL_VERSION, 'node_id': rm.node_id, 'name': gethostname(), 'role': rm.role,
                'adoptable': rm.role == 'client' and not rm.is_paired_client, 'cert': rm.ensure_identity(),
                'web_port': cmdargs.port_secure, 'link_port': cmdargs.port_link}

    # ── the server asks a client to join ──────────────────────────────────

    async def probe(self, host: str, port: int = 443) -> dict:
        """Look at a device before adopting it: who it says it is, and the fingerprint of the certificate it presents,
        which the user compares with the one on the device's own page."""
        host, port = pairing.clean_host(host), pairing.clean_port(port, cmdargs.port_secure)
        hello = pairing.parse_hello(await pairing.call(host, port, '/remote/hello'))
        if hello['node_id'] == self.rm.node_id:
            raise PairingError('That is this very node')
        return {'host': host, 'port': port, 'name': hello['name'], 'node_id': hello['node_id'],
                'fingerprint': ident.format_fingerprint(hello['fingerprint']), 'role': hello['role'],
                'adoptable': hello['adoptable']}

    async def adopt(self, host: str, port: int, fingerprint: str):
        """Pair with the unpaired client at host:port, provided it still presents the certificate the user confirmed."""
        rm = self.rm
        if rm.role != 'server':
            raise PairingError('This node is a client: it cannot adopt other nodes')
        host, port = pairing.clean_host(host), pairing.clean_port(port, cmdargs.port_secure)
        hello = pairing.parse_hello(await pairing.call(host, port, '/remote/hello'))
        if hello['fingerprint'] != _plain_fingerprint(fingerprint):
            raise PairingError('The device presented a different certificate than the one you confirmed: '
                               'something may be in the way, nothing was paired')
        if not hello['adoptable']:
            raise PairingError('That device is not an unpaired client (is it in client mode, and not paired already?)')
        if hello['node_id'] == rm.node_id:
            raise PairingError('That is this very node')

        # pinned first: the client connects the moment it accepts, and must find the door open
        peer = rm.pin_client(hello['cert'], hello['name'], host, hello['web_port'])
        self.link.pin(hello['cert'])
        try:
            answer = pairing.parse_hello(await pairing.call(host, port, '/remote/adopt', {
                'v': pairing.PROTOCOL_VERSION, 'cert': rm.ensure_identity(), 'name': gethostname(),
                'web_port': cmdargs.port_secure, 'link_port': cmdargs.port_link,
                'expect_fingerprint': hello['fingerprint']}))
            if answer['fingerprint'] != hello['fingerprint']:
                raise PairingError('The device changed its certificate while pairing, nothing was paired')
        except Exception:
            rm.forget_client(peer.id)
            await self.link.reload()
            raise
        self.requests.remove(peer.id)
        self.browser.found.pop(peer.id, None)
        logger.success('Adopted {} ({})', peer.name, host)
        self.changed()

    # ── a client asks a server to adopt it (the server's administrator decides) ──

    async def request_pairing(self, host: str, port: int = 443):
        rm = self.rm
        if rm.role != 'client' or rm.is_paired_client:
            raise PairingError('Only an unpaired client can ask a server for pairing')
        host, port = pairing.clean_host(host), pairing.clean_port(port, cmdargs.port_secure)
        hello = pairing.parse_hello(await pairing.call(host, port, '/remote/hello'))
        if hello['role'] != 'server':
            raise PairingError('That device is not a server')
        answer = pairing.parse_hello(await pairing.call(host, port, '/remote/pair-request', {
            'v': pairing.PROTOCOL_VERSION, 'cert': rm.ensure_identity(), 'name': gethostname(),
            'web_port': cmdargs.port_secure, 'link_port': cmdargs.port_link}))
        if answer['fingerprint'] != hello['fingerprint']:
            raise PairingError('The device presented two different certificates, nothing was requested')
        self.pending_server = {'ip': host, 'port': answer['web_port'], 'link_port': answer['link_port'],
                               'name': answer['name'], 'cert': answer['cert'], 'fingerprint': answer['fingerprint'],
                               'at': time.time()}
        self.changed()

    def cancel_pairing(self):
        self.pending_server = None
        self.changed()

    def _pending_server(self) -> Optional[dict]:
        pending = self.pending_server
        if pending and pending['at'] < time.time() - PENDING_SERVER_TTL:
            self.pending_server = pending = None
        return pending

    async def approve(self, node_id: str):
        request = self.requests.get(node_id)
        if request is None:
            raise PairingError('That request is no longer there')
        await self.adopt(request.ip, request.web_port, request.fingerprint)

    def reject(self, node_id: str):
        self.requests.remove(node_id)
        self.changed()

    # ── what the other side of those calls does (called by routers/pairing.py) ──

    async def accept_adoption(self, body: dict, from_ip: str) -> dict:
        """A server wants to adopt this node. Only an unpaired client says yes."""
        rm = self.rm
        if rm.role != 'client':
            raise PairingError('This node is not in client mode')
        if rm.is_paired_client:
            raise PairingError('This node is already paired: unpair it first')
        cert = pairing.clean_cert(body.get('cert'))
        fp = ident.fingerprint(cert)
        if body.get('expect_fingerprint') != rm.fingerprint:
            raise PairingError('This adoption was meant for another device')
        pending = self._pending_server()
        if pending and pending['fingerprint'] != fp:
            # this node asked one particular server: nobody else may take it while it waits
            raise PairingError('This node is waiting to be adopted by another server')
        try:
            ip = pairing.clean_host(from_ip)
        except PairingError:
            raise PairingError('Servers are reached over IPv4') from None
        rm.role = 'client'
        rm.server_ip = ip
        rm.server_port = pairing.clean_port(body.get('web_port'), cmdargs.port_secure)
        rm.server_link_port = pairing.clean_port(body.get('link_port'), cmdargs.port_link)
        rm.server_id = ident.node_id(cert)
        rm.server_name = pairing.clean_name(body.get('name'))
        rm.server_cert = cert
        rm.save()
        logger.success('Adopted by the server {} ({})', rm.server_name, ip)
        asyncio.get_running_loop().create_task(self.apply_role())
        return self.hello()

    async def accept_request(self, body: dict, from_ip: str) -> dict:
        """A client asks this node to adopt it: put the request in front of the administrator."""
        rm = self.rm
        if rm.role != 'server':
            raise PairingError('This node is a client, not a server')
        cert = pairing.clean_cert(body.get('cert'))
        if ident.node_id(cert) == rm.node_id:
            raise PairingError('That is this very node')
        if rm.peer_by_fingerprint(ident.fingerprint(cert)):
            raise PairingError('This node is already paired with this server')
        self.requests.add(PairRequest(
                node_id=ident.node_id(cert), name=pairing.clean_name(body.get('name')), ip=pairing.clean_host(from_ip),
                web_port=pairing.clean_port(body.get('web_port'), 443),
                link_port=pairing.clean_port(body.get('link_port'), 4443), cert=cert))
        self.changed()
        return self.hello()

    # ── changing roles ────────────────────────────────────────────────────

    async def become_client(self):
        rm = self.rm
        if rm.role == 'client':
            return
        if rm.peers:
            raise PairingError('This node has clients: unpair them before turning it into a client')
        rm.role = 'client'
        rm.save()
        await self.apply_role()

    async def become_server(self):
        rm = self.rm
        if rm.is_paired_client:
            raise PairingError('This node belongs to a server: leave it first')
        if rm.role == 'server':
            return
        rm.role = 'server'
        rm.save()
        await self.apply_role()

    async def unpair_client(self, client_id: str):
        rm = self.rm
        peer = rm.peers.get(client_id)
        if peer is None:
            raise PairingError('Unknown client')
        await self.link.disconnect(client_id, reason='Unpaired by the server')
        rm.forget_client(client_id)
        await self.link.reload()        # the certificate leaves the listener for good
        logger.info('Unpaired {}', peer.name)
        self.changed()

    async def forget_server(self):
        """Leave the server this node belongs to, without anything else (see the transfer for the version that takes the
        playlists along): the node goes back to being an unpaired client."""
        rm = self.rm
        for field in ('server_ip', 'server_link_port', 'server_id', 'server_name', 'server_cert'):
            setattr(rm, field, None)
        rm.server_port = cmdargs.port_secure
        rm.save()
        await self.apply_role()

    # ── state for the web UI ──────────────────────────────────────────────

    def state(self) -> dict:
        rm = self.rm
        connected = self.link.connected()
        out = {
            'role': rm.role,
            'name': gethostname(),
            'node_id': rm.node_id,
            'fingerprint': ident.format_fingerprint(rm.fingerprint),
            'link_port': cmdargs.port_link if self.link.running else None,
            'discovery': DISCOVERY_AVAILABLE,
            'clients': [], 'discovered': [], 'requests': [],
            'server': None, 'pairing': None,
        }
        if rm.role == 'server':
            out['clients'] = [{
                'id': peer.id, 'name': peer.name, 'ip': peer.ip, 'port': peer.port, 'enabled': peer.enabled,
                'connected': peer.id in connected, 'fingerprint': ident.format_fingerprint(peer.fingerprint),
            } for peer in rm.peers.values()]
            out['discovered'] = [{'id': a.node_id, 'name': a.name, 'host': a.host, 'port': a.port}
                                 for a in self.browser.found.values() if a.node_id not in rm.peers]
            out['requests'] = [{
                'id': r.node_id, 'name': r.name, 'ip': r.ip, 'port': r.web_port,
                'fingerprint': ident.format_fingerprint(r.fingerprint),
            } for r in self.requests.all()]
        elif rm.is_paired_client:
            out['server'] = {
                'ip': str(rm.server_ip), 'port': rm.server_port, 'name': rm.server_name,
                'fingerprint': ident.format_fingerprint(rm.server_fingerprint),
                'state': self.uplink.state, 'error': self.uplink.error,
            }
        elif pending := self._pending_server():
            out['pairing'] = {'ip': pending['ip'], 'name': pending['name'],
                              'fingerprint': ident.format_fingerprint(pending['fingerprint'])}
        return out

    @staticmethod
    def admins() -> list[str]:
        """The connected web UI users who may see how this node is paired: the page is administrative, and what it
        shows (addresses, fingerprints, pending requests) is nobody else's business."""
        from utils.models.user import user_manager
        return [name for name in list(WS.active_users)
                if WS.active_users[name] and (user := user_manager.get_user(name)) and user.has_perm.admin]

    def changed(self):
        """Something the Remote page shows is different now: push it to every open page (no polling, no request)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._push_task is None or self._push_task.done():
            self._push_task = loop.create_task(self._push())

    async def _push(self):
        await asyncio.sleep(0)       # several changes in the same instant make one message
        if admins := self.admins():
            await WS.multicast(admins, 'Settings/Remote/mode', **self.state())


service = RemoteService()
