from loguru import logger

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSMulticast, WSResponse
from utils.models.remote import RemoteManager


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
