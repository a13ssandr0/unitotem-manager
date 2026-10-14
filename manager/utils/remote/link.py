"""
The link between a server and its clients: a websocket over TLS with certificates pinned on both ends (utils.remote.tls).

The listener (server side) is a port of its own and not the web port: browsers must keep working on 443 with no client
certificate, while this one must refuse anything that is not a paired node *during the TLS handshake*, before a single
byte of HTTP is read. Hypercorn cannot do that (it does not tell the application which certificate the peer presented),
the `websockets` server can: it gives access to the TLS object of every connection.

What flows over it: the server's commands to the client's viewer (the same frames the local viewer receives), the
client's report of its screens and windows, and the control messages of the data transfer (utils.remote.transfer).
"""
from __future__ import annotations

import asyncio
import json
import ssl
from base64 import b64decode
from http import HTTPStatus
from socket import gethostname
from types import SimpleNamespace
from typing import Awaitable, Callable, Optional

from loguru import logger
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus, WebSocketException

from api.commons import SHUTDOWN_EVENT
from utils.models.command_line import cmdargs
from utils.models.remote import Peer, RemoteManager
from utils.remote import viewers
from utils.remote.tls import client_context, peer_fingerprint, server_context

MAX_MESSAGE = 2 * 1024 * 1024       # one frame of a file transfer is far smaller, this is the ceiling the peer may use
CLOSE_UNPAIRED = 4403               # the server does not know (any more) the certificate presented
CLOSE_REPLACED = 4409               # another connection of the same node took over


CONTROL_PREFIX = 'link:'            # text frames of the data transfer; a viewer command never starts like this


def is_control(raw) -> bool:
    return isinstance(raw, bytes) or raw.startswith(CONTROL_PREFIX)


def control_frame(**fields) -> str:
    return CONTROL_PREFIX + json.dumps(fields)


def parse_control(raw: str) -> dict:
    return json.loads(raw.removeprefix(CONTROL_PREFIX))


def decode_frame(raw: str) -> dict:
    """The server signs its commands for viewers: frames are 'b64(json).b64(signature)' (see WSManager.prepare_message).
    On the link the signature adds nothing to what pinned TLS already gives, so only the payload is read."""
    if raw.startswith('{'):
        return json.loads(raw)
    data, _, _signature = raw.partition('.')
    return json.loads(b64decode(data))


class LinkSocket:
    """What WSManager expects of a connection, on top of a websockets one"""

    def __init__(self, ws, peer_id: str, host: str):
        self.ws = ws
        self.headers = {'instance_id': peer_id}
        self.client = SimpleNamespace(host=host)

    async def accept(self):
        """already accepted by the handshake"""

    async def send_text(self, text: str):
        await self.ws.send(text)

    async def close(self, code: int = 1000, reason: str = ''):
        await self.ws.close(code, reason)


class LinkServer:
    """The listener of a server node. Only paired and enabled clients get past the handshake."""

    def __init__(self, remote_ws, changed: Callable[[], None]):
        self.remote_ws = remote_ws
        self.changed = changed
        self._server = None
        self._ctx: Optional[ssl.SSLContext] = None
        self._sockets: dict[str, LinkSocket] = {}
        # set by utils.remote.transfer: receives the control messages of a client (text with a "link" key, or bytes)
        self.on_control: Optional[Callable[[Peer, LinkSocket, object], Awaitable]] = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def connected(self) -> set[str]:
        return set(self._sockets)

    def socket_of(self, peer_id: str) -> Optional[LinkSocket]:
        return self._sockets.get(peer_id)

    async def start(self):
        rm = RemoteManager.get_instance()
        if self._server is not None or rm.role != 'server':
            return
        ctx = self._ctx = server_context(rm.ensure_identity(), rm.identity_key, rm.trusted_client_certs())
        try:
            self._server = await serve(self._handle, str(cmdargs.bind_link), cmdargs.port_link, ssl=ctx,
                                       process_request=self._process_request, max_size=MAX_MESSAGE, compression=None)
        except OSError as e:
            # the web UI and the scheduler must not depend on this: a node whose port is taken is a standalone one
            logger.error('Cannot listen for clients on port {}: {}', cmdargs.port_link, e)
            return
        logger.info('Listening for paired clients on port {}', cmdargs.port_link)

    def pin(self, cert_pem: str):
        """A client was just paired: let its certificate through without restarting the listener (which would drop the
        clients that are connected). Removing one needs `reload`."""
        if self._ctx is not None:
            self._ctx.load_verify_locations(cadata=cert_pem)

    async def stop(self):
        server, self._server, self._ctx = self._server, None, None
        if server is not None:
            server.close()          # also closes the connections: nobody is left talking to a context that is going away
            await server.wait_closed()

    async def reload(self):
        """After the set of paired clients changed: a new context is needed (a certificate cannot be removed from a live one)"""
        await self.stop()
        await self.start()

    async def disconnect(self, peer_id: str, code: int = CLOSE_UNPAIRED, reason: str = 'Disconnected by the server'):
        sock = self._sockets.get(peer_id)
        if sock is not None:
            await sock.close(code, reason)

    # the application-level check, in the HTTP request of the upgrade: refuses before the websocket exists. It is the
    # second wall: the handshake already refused every certificate that was never pinned, this one refuses those that
    # were and are not any more (a certificate stays in the live context until the listener is reloaded), and the ones
    # of a client the user switched off.
    def _process_request(self, connection, request):
        fp = peer_fingerprint(connection.transport.get_extra_info('ssl_object'))
        peer = RemoteManager.get_instance().peer_by_fingerprint(fp) if fp else None
        if peer is None or not peer.enabled or request.path != '/remote':
            logger.warning('Refused a connection to the link (certificate {}, path {})', fp, request.path)
            return connection.respond(HTTPStatus.FORBIDDEN, 'Forbidden\n')
        return None

    async def _handle(self, ws):
        rm = RemoteManager.get_instance()
        peer = rm.peer_by_fingerprint(peer_fingerprint(ws.transport.get_extra_info('ssl_object')) or '')
        if peer is None or not peer.enabled:
            await ws.close(CLOSE_UNPAIRED)
            return
        host = (ws.remote_address or ('?',))[0]
        headers = ws.request.headers
        hostname = headers.get('hostname') or peer.name
        port = viewers.parse_port(headers.get('port'), peer.port)
        if (peer.ip, peer.name, peer.port) != (host, hostname, port):
            peer.ip, peer.name, peer.port = host, hostname[:128], port
            rm.save()

        old = self._sockets.get(peer.id)
        if old is not None:
            await old.close(CLOSE_REPLACED, 'Replaced by a newer connection')
        sock = LinkSocket(ws, peer.id, host)
        self._sockets[peer.id] = sock
        await self.remote_ws.connect(sock, user=peer.id)
        logger.success('Client {} ({}) connected', peer.name, host)
        await viewers.viewer_connected(peer.id, host, port, peer.name)
        self.changed()
        try:
            async for raw in ws:
                if not is_control(raw):
                    await viewers.viewer_message(peer.id, raw)
                elif self.on_control is not None:
                    await self.on_control(peer, sock, raw)
        except ConnectionClosed:
            pass
        except Exception:
            logger.exception('Client {} misbehaved', peer.name)
        finally:
            if self._sockets.get(peer.id) is sock:
                del self._sockets[peer.id]
                self.remote_ws.disconnect(sock, user=peer.id)
                await viewers.viewer_gone(peer.id)
                logger.info('Client {} disconnected', peer.name)
                self.changed()


class ClientUplink:
    """The connection of a client node to its server, and the bridge between it and the viewer of this node: what the
    server says is handed to the local viewer, what the local viewer reports goes up to the server."""

    def __init__(self, relay: Callable[[str, dict], Awaitable], changed: Callable[[], None]):
        self.relay = relay
        self.changed = changed
        self.state = 'idle'     # idle | connecting | connected | rejected | unreachable
        self.error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self._last_info: Optional[str] = None       # what the local viewer last said about its windows
        self.on_control: Optional[Callable[[object], Awaitable]] = None
        self.on_connected: Optional[Callable[[], None]] = None

    def _set(self, state: str, error: Optional[str] = None):
        if (state, error) != (self.state, self.error):
            self.state, self.error = state, error
            self.changed()

    @property
    def connected(self) -> bool:
        return self.state == 'connected'

    @property
    def last_info(self) -> Optional[str]:
        return self._last_info

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name='remote_control')

    async def stop(self):
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._set('idle')

    async def forward(self, raw: str):
        """The local viewer told something to its manager: the server needs to know too."""
        if '"WebviewInfo"' in raw[:60]:
            self._last_info = raw
        if self._ws is not None:
            try:
                await self._ws.send(raw)
            except ConnectionClosed:
                pass

    async def viewer_gone(self):
        self._last_info = None
        await self.forward(viewers.EMPTY_INFO)

    async def send(self, data):
        if self._ws is None:
            raise ConnectionError('Not connected to the server')
        await self._ws.send(data)

    async def _run(self):
        delay = 2
        while not SHUTDOWN_EVENT.is_set():
            rm = RemoteManager.get_instance()
            if not rm.is_paired_client:
                self._set('idle')
                return
            uri = f'wss://{rm.server_ip}:{rm.server_link_port or cmdargs.port_link}/remote'
            pinned = rm.server_cert
            try:
                self._set('connecting')
                ctx = client_context(rm.ensure_identity(), rm.identity_key, pinned)
                async with connect(
                        uri, ssl=ctx, open_timeout=10, max_size=MAX_MESSAGE, compression=None,
                        additional_headers={'hostname': gethostname(), 'port': str(cmdargs.port_secure)}) as ws:
                    # OpenSSL already refused any other certificate; this is the same check made by the application,
                    # so that a context built wrongly one day cannot silently turn into "trust whoever answers"
                    if peer_fingerprint(ws.transport.get_extra_info('ssl_object')) != rm.server_fingerprint:
                        raise ssl.SSLError('The server presented a certificate that is not the paired one')
                    self._ws = ws
                    delay = 2
                    self._set('connected')
                    logger.success('Connected to the server {}', rm.server_ip)
                    if self._last_info:
                        await ws.send(self._last_info)
                    if self.on_connected:
                        self.on_connected()
                    async for raw in ws:
                        await self._on_message(raw)
            except asyncio.CancelledError:
                raise
            except InvalidStatus as e:
                code = e.response.status_code
                # 403: the server answers but does not (or no longer) know this node
                self._set('rejected' if code == 403 else 'unreachable', f'The server answered HTTP {code}')
            except InvalidMessage:
                # what a server that refuses our certificate in the handshake looks like from here: it hangs up
                self._set('rejected', 'The server does not accept this node: it may have been unpaired there')
            except ssl.SSLCertVerificationError as e:
                logger.error('The server at {} does not present the certificate it was paired with: {}', rm.server_ip, e)
                self._set('unreachable', 'The server presented a certificate that is not the paired one')
            except (OSError, ssl.SSLError, WebSocketException, asyncio.TimeoutError) as e:
                # a server that dropped the handshake for lack of a pinned client certificate looks like this too
                logger.warning('Server {} unavailable: {}', rm.server_ip, e)
                self._set('unreachable', str(e) or e.__class__.__name__)
            except Exception:
                logger.exception('Uplink failed')
                self._set('unreachable', 'Unexpected error, see the log')
            finally:
                if self._ws is not None:
                    self._ws = None
                    self._set('connecting')
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _on_message(self, raw):
        if is_control(raw):
            if self.on_control is not None:
                await self.on_control(raw)
            return
        try:
            data = decode_frame(raw)
            data = dict(data)
            target = data.pop('target')
        except (ValueError, KeyError):
            logger.warning('Undecodable frame from the server')
            return
        await self.relay(target, data)
