"""
The running side of remote control: owns the listener (server role) or the uplink (client role) of this node, and tells
the web UI whenever something that it shows changes. One per process: `service`.
"""
from __future__ import annotations

import asyncio
from socket import gethostname
from typing import Optional

from loguru import logger

from api.ws.endpoints import REMOTE_WS, WS
from utils.models.remote import RemoteManager
from utils.remote import identity as ident
from utils.remote.link import ClientUplink, LinkServer


class RemoteService:
    def __init__(self):
        self.link = LinkServer(REMOTE_WS, self.changed)
        self.uplink = ClientUplink(self._relay, self.changed)
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
            await self.uplink.stop()
            await self.link.reload()
        else:
            await self.link.stop()
            if rm.is_paired_client:
                self.uplink.start()
            else:
                await self.uplink.stop()
        self.changed()

    async def stop(self):
        await self.uplink.stop()
        await self.link.stop()

    # ── towards the viewer of this node ───────────────────────────────────

    @staticmethod
    async def _relay(target: str, data: dict):
        # nocache: the server resends what a window needs when the window (re)registers; a copy kept here would be stale
        await REMOTE_WS.broadcast(target, nocache=True, **data)

    # ── state for the web UI ──────────────────────────────────────────────

    def state(self) -> dict:
        rm = self.rm
        connected = self.link.connected()
        out = {
            'role': rm.role,
            'name': gethostname(),
            'node_id': rm.node_id,
            'fingerprint': ident.format_fingerprint(rm.fingerprint),
            'link_port': None,
            'clients': [],
            'server': None,
        }
        if rm.role == 'server':
            out['link_port'] = self.link_port if self.link.running else None
            out['clients'] = [{
                'id': peer.id, 'name': peer.name, 'ip': peer.ip, 'port': peer.port, 'enabled': peer.enabled,
                'connected': peer.id in connected, 'fingerprint': ident.format_fingerprint(peer.fingerprint),
            } for peer in rm.peers.values()]
        elif rm.is_paired_client:
            out['server'] = {
                'ip': str(rm.server_ip), 'port': rm.server_port, 'name': rm.server_name,
                'fingerprint': ident.format_fingerprint(rm.server_fingerprint),
                'state': self.uplink.state, 'error': self.uplink.error,
            }
        return out

    @property
    def link_port(self) -> int:
        from utils.models.command_line import cmdargs
        return cmdargs.port_link

    def changed(self):
        """Something the Remote page shows is different now: push it to every open page (no polling, no request)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._push_task is None or self._push_task.done():
            self._push_task = loop.create_task(self._push())

    @staticmethod
    def admins() -> list[str]:
        """The connected web UI users who may see how this node is paired: the page is administrative, and what it
        shows (addresses, fingerprints, pending requests) is nobody else's business."""
        from utils.models.user import user_manager
        return [name for name in list(WS.active_users)
                if WS.active_users[name] and (user := user_manager.get_user(name)) and user.has_perm.admin]

    async def _push(self):
        await asyncio.sleep(0)       # several changes in the same instant make one message
        if admins := self.admins():
            await WS.multicast(admins, 'Settings/Remote/mode', **self.state())


service = RemoteService()
