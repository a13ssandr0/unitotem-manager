from loguru import logger

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSMulticast, WSResponse
from utils.models.remote import RemoteManager
from utils.remote.pairing import PairingError


def _service():
    # deferred: utils.remote.service imports the API registry, which is importing this module
    from utils.remote.service import service
    return service


class Remote(WSAPIBase):
    """Remote control: this node as a server of other nodes, or as a client of one. Every function is administrative
    (no `perms` means admin): how the nodes are paired decides who can put what on which screen."""

    def mode(self):
        """How this node is paired: its role and identity, and its clients (server) or its server (client)."""
        return WSMulticast(_service().admins(), **_service().state())

    def key_status(self):
        """State of the RSA key that signs the commands for the viewer: missing, generating or ready"""
        return WSMulticast(_service().admins(), status=RemoteManager.signing_key_status())

    # ── role ──

    async def become_client(self):
        """Turn this node into a client: unpaired, it announces itself on the network so that a server can adopt it, and
        it can ask a server for pairing by address."""
        try:
            await _service().become_client()
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    async def become_server(self):
        """Back to a server (the default). A client that belongs to a server has to leave it first."""
        try:
            await _service().become_server()
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    # ── server side ──

    async def probe(self, host: str, port: int = 443):
        """Look at a device before adopting it. The answer carries the fingerprint of its certificate: the user compares
        it with the one on the device's own page and only then adopts it."""
        try:
            return WSResponse(**await _service().probe(host, port))
        except PairingError as e:
            return WSResponse(error=str(e))

    async def adopt(self, host: str, port: int, fingerprint: str):
        """Pair with an unpaired client. `fingerprint` is the one the user confirmed: if the device presents another
        certificate, nothing is paired."""
        try:
            await _service().adopt(host, port, fingerprint)
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    async def approve_request(self, client_id: str):
        """Accept a client that asked to be adopted."""
        try:
            await _service().approve(client_id)
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    def reject_request(self, client_id: str):
        _service().reject(client_id)

    async def unpair_client(self, client_id: str, force: bool = False):
        """Unpair a client: what its screens were showing goes back to it (and is removed here if nothing else uses it),
        then its certificate is no longer accepted. `force` forgets a client that cannot receive its content (offline,
        broken): the content stays here."""
        try:
            await _service().unpair_client(client_id, force)
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    # ── client side ──

    async def request_pairing(self, server_ip: str, server_port: int = 443):
        """Ask the server at this address to adopt this node; its administrator has to approve."""
        try:
            await _service().request_pairing(server_ip, server_port)
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    def cancel_pairing(self):
        _service().cancel_pairing()

    async def leave_server(self):
        """Leave the server this node belongs to: the server sends back what this node's screens were showing."""
        try:
            await _service().leave_server()
        except PairingError as e:
            return WSResponse(error=str(e))
        return None

    async def forget_server(self):
        """Leave the server without moving anything (it is gone or does not answer; it keeps this node as an offline
        client until somebody unpairs it there). This node becomes a standalone one with what it has locally."""
        await _service().forget_server()

    async def set_client_enabled(self, client_id: str, enabled: bool):
        """Let a paired client connect again or, with enabled=False, refuse it (and drop it now) without unpairing it."""
        service = _service()
        peer = service.rm.peers.get(client_id)
        if peer is None:
            return WSResponse(error='Unknown client')
        peer.enabled = enabled
        service.rm.save()
        if not enabled:
            await service.link.disconnect(client_id, reason='Disabled by the server')
        logger.info('Client {} {}', peer.name, 'enabled' if enabled else 'disabled')
        service.changed()
        return None
