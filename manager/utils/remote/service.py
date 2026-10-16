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
from utils.notifications import notifications
from utils.remote import bundle, pairing, transfer, viewers
from utils.remote import identity as ident
from utils.remote.discovery import AVAILABLE as DISCOVERY_AVAILABLE, Announcer, Browser
from utils.remote.link import ClientUplink, LinkServer, control_frame
from utils.remote.pairing import PairingError, PairRequest, PairRequests, RateLimiter
from utils.remote.transfer import Channel, Receiver, TransferError, receive_bundle, send_bundle
from utils.storage.uploadmanager import upload_manager

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
        # the control traffic of the transfers: one channel per live connection
        self._channels: dict[object, Channel] = {}
        self._committed: set[str] = set()
        self._migrating = False
        self.notice: Optional[str] = None       # the last thing that went wrong that the page should say
        self._seen: set[str] = set()            # clients that were connected during this run
        self._offline_keys: set[str] = set()    # notifications raised for clients that went away
        self.link.on_control = self._server_control
        self.uplink.on_control = self._client_control
        self.uplink.on_connected = self._uplink_connected

    @property
    def rm(self) -> RemoteManager:
        return RemoteManager.get_instance()

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self):
        """Bring up whatever the saved role needs. Never raises: remote control failing must not take the node with it."""
        try:
            self.rm.ensure_identity()
            transfer.clean_stale(self._stage_root)
            self._load_committed()
            await self.apply_role()
        except Exception:
            logger.exception('Remote control did not start, the node keeps running on its own')
            notifications.notify('remote.start', 'error', 'Remote control did not start',
                                 'The node keeps running on its own. The log has the details.', source='remote',
                                 audience='admin', link='/settings/remote')

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
                # the windows of this machine belong to the server now: the local scheduler must stop driving them
                await viewers.viewer_gone(bundle.LOCAL_VIEWER)
                self.uplink.start()
            else:
                await self.uplink.stop()
                await self.announcer.start(rm.node_id, gethostname(), cmdargs.port_secure, cmdargs.port_link)
        if not rm.is_paired_client:
            await self._attach_local_viewer()
        self.changed()

    async def _attach_local_viewer(self):
        """This node drives its own windows: introduce the viewer to the local scheduler (again, if it was connected
        while the node belonged to a server and so was kept away from it)."""
        info = self.uplink.last_info
        if info:
            await viewers.viewer_connected(bundle.LOCAL_VIEWER, '127.0.0.1', cmdargs.port_secure, gethostname())
            await viewers.viewer_message(bundle.LOCAL_VIEWER, info)

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

        self._reset_committed()
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
        rm.migrate_on_connect = True
        self._reset_committed()
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

    async def unpair_client(self, client_id: str, force: bool = False):
        """Unpair a client. Its playlists and uploads go back to it first (what only it was showing is removed here
        afterwards, what anything else uses stays); if that cannot be done nothing changes, unless `force`, which
        forgets the client without moving anything (the client is offline, or broken)."""
        rm = self.rm
        peer = rm.peers.get(client_id)
        if peer is None:
            raise PairingError('Unknown client')
        sock = self.link.socket_of(client_id)
        if sock is not None:
            try:
                await self._hand_back(peer, sock)
            except (TransferError, ConnectionError) as e:
                if not force:
                    raise PairingError(f'The content could not be moved to the client, nothing was changed: {e}') from None
                logger.warning('Unpairing {} without moving its content: {}', peer.name, e)
        elif not force:
            raise PairingError('The client is offline, so its content cannot be moved to it. Unpair it anyway '
                               'to forget it and keep the content here.')
        await self.link.disconnect(client_id, reason='Unpaired by the server')
        rm.forget_client(client_id)
        await self.link.reload()        # the certificate leaves the listener for good
        self._reset_committed()
        logger.info('Unpaired {}', peer.name)
        self.changed()

    async def _hand_back(self, peer, sock):
        plan = bundle.plan_for(peer.id)
        channel = self._channel_for(sock, sock.ws.send, peer)
        if plan.playlists:
            manifest = await bundle.build_manifest(plan, scope='leave:' + peer.id)
            await send_bundle(channel, manifest, upload_manager.folder)
            await bundle.remove_handed_over(plan, peer.id)
        await sock.ws.send(control_frame(link='unpaired'))

    async def forget_server(self):
        """Leave the server without anything moving (the server is gone, or does not answer): the node is standalone
        again, with whatever it has locally."""
        rm = self.rm
        for field in ('server_ip', 'server_link_port', 'server_id', 'server_name', 'server_cert'):
            setattr(rm, field, None)
        rm.server_port = cmdargs.port_secure
        rm.migrate_on_connect = False
        rm.role = 'server'
        self._reset_committed()
        rm.save()
        await self.apply_role()

    async def leave_server(self):
        """Ask the server to let this node go: it sends back what this node's windows were showing and unpairs us."""
        if not self.uplink.connected:
            raise PairingError('Not connected to the server: use "Forget the server" to leave without moving anything')
        await self.uplink.send(control_frame(link='leave-request'))

    # ── transfers ─────────────────────────────────────────────────────────

    @property
    def _stage_root(self):
        return upload_manager.folder.parent / 'transfer'

    def _load_committed(self):
        import json
        try:
            self._committed = set(json.loads((self._stage_root / 'committed.json').read_text()))
        except (OSError, ValueError):
            self._committed = set()

    def _reset_committed(self):
        """A transfer id is only meant to recognise a retry of the same attempt. Every change of who is paired with
        whom starts a new story: the same content moving again later is a new transfer, not a repetition."""
        self._committed.clear()
        try:
            (self._stage_root / 'committed.json').unlink(missing_ok=True)
        except OSError:
            pass

    def _remember_committed(self, tid: str):
        import json
        self._committed.add(tid)
        try:
            self._stage_root.mkdir(parents=True, exist_ok=True)
            (self._stage_root / 'committed.json').write_text(json.dumps(sorted(self._committed)[-100:]))
        except OSError:
            logger.warning('Cannot record the transfer {} as applied', tid)

    def _receiver(self, viewer_id: str) -> Receiver:
        async def apply(manifest, staged):
            await bundle.apply_bundle(manifest, staged, viewer_id)
            self._remember_committed(manifest['id'])

        return Receiver(self._stage_root, upload_manager.folder, apply, self._committed)

    def _channel_for(self, key, send, peer=None) -> Channel:
        channel = self._channels.get(key)
        if channel is None:
            if len(self._channels) > 16:        # connections that are gone
                for old in list(self._channels)[:8]:
                    self._channels.pop(old).close()
            channel = self._channels[key] = Channel(send)
            viewer_id = peer.id if peer else bundle.LOCAL_VIEWER
            channel.on_begin = lambda ch, first: receive_bundle(ch, first, self._receiver(viewer_id))
            channel.on_other = (lambda msg: self._server_other(peer, msg)) if peer else self._client_other
        return channel

    async def _server_control(self, peer, sock, raw):
        await self._channel_for(sock, sock.ws.send, peer).dispatch(raw)

    async def _client_control(self, raw):
        ws = self.uplink._ws
        if ws is not None:
            await self._channel_for(ws, ws.send).dispatch(raw)

    async def _server_other(self, peer, msg: dict):
        if msg.get('link') == 'leave-request':
            async def leave():
                try:
                    await self.unpair_client(peer.id)
                except PairingError as e:
                    sock = self.link.socket_of(peer.id)
                    if sock is not None:
                        await sock.ws.send(control_frame(link='leave-refused', reason=str(e)[:300]))

            asyncio.get_running_loop().create_task(leave(), name='leave')

    async def _client_other(self, msg: dict):
        kind = msg.get('link')
        if kind == 'unpaired':
            logger.success('The server released this node')
            await self.forget_server()
        elif kind == 'leave-refused':
            self.notice = str(msg.get('reason') or 'The server refused')[:300]
            self.changed()

    def _uplink_connected(self):
        if self.rm.migrate_on_connect and not self._migrating:
            asyncio.get_running_loop().create_task(self._migrate(), name='migrate')

    async def _migrate(self):
        """Just adopted: move what this node has to the server, restoring which of its windows play which playlist."""
        rm = self.rm
        self._migrating = True
        try:
            if bundle.is_empty():
                rm.migrate_on_connect = False
                rm.save()
                return
            ws = self.uplink._ws
            if ws is None:
                return
            channel = self._channel_for(ws, ws.send)
            manifest = await bundle.build_manifest(bundle.plan_everything(), scope='adopt:' + rm.node_id)
            await send_bundle(channel, manifest, upload_manager.folder)
            await bundle.remove_everything()
            rm.migrate_on_connect = False
            rm.save()
            self.notice = None
            logger.success('The content of this node is on the server now')
        except (TransferError, ConnectionError) as e:
            # nothing was removed here: it will be tried again the next time the connection comes up
            self.notice = f'The content could not be moved to the server: {e}'
            logger.error(self.notice)
        except Exception:
            logger.exception('Moving the content to the server failed')
            self.notice = 'The content could not be moved to the server (see the log)'
        finally:
            self._migrating = False
            self.changed()

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
            'server': None, 'pairing': None, 'notice': self.notice, 'migrating': self._migrating,
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

    def _sync_notifications(self):
        """What the Remote page shows, as far as it needs somebody to act: requests waiting for approval, a client that
        was connected and went away, a server that cannot be reached. Derived from the state every time it changes, so
        each one resolves itself when the situation does (nothing is raised for a client that never connected during
        this run: right after a boot every known client is offline until it reconnects)."""
        rm = self.rm
        link = '/settings/remote'
        requests = self.requests.all() if rm.role == 'server' else []
        if requests:
            names = ', '.join(r.name for r in requests[:5]) + (' and more' if len(requests) > 5 else '')
            notifications.notify('remote.pair-requests', 'info', 'Devices ask to be paired',
                                 f'{len(requests)} waiting for approval: {names}', source='remote', audience='admin',
                                 link=link)
        else:
            notifications.resolve('remote.pair-requests')

        connected = self.link.connected() if rm.role == 'server' else set()
        self._seen |= connected
        wanted = set()
        for peer in rm.peers.values() if rm.role == 'server' else ():
            if peer.enabled and peer.id in self._seen and peer.id not in connected:
                key = f'remote.client-offline.{peer.id}'
                wanted.add(key)
                notifications.notify(key, 'warning', 'A client is not connected', f'{peer.name} ({peer.ip}) went away.',
                                     source='remote', audience='admin', link=link)
        for key in self._offline_keys - wanted:
            notifications.resolve(key)
        self._offline_keys = wanted

        if rm.is_paired_client and self.uplink.state in ('rejected', 'unreachable'):
            why = 'The server does not accept this node' if self.uplink.state == 'rejected' else 'The server cannot be reached'
            notifications.notify('remote.server', 'warning', why, f'{rm.server_name} ({rm.server_ip})',
                                 source='remote', audience='admin', link=link)
        else:
            notifications.resolve('remote.server')

    def changed(self):
        """Something the Remote page shows is different now: push it to every open page (no polling, no request)."""
        try:
            self._sync_notifications()
        except Exception:
            logger.exception('Cannot update the remote control notifications')
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
