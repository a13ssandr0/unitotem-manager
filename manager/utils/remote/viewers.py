"""
What happens to the viewers (the windows of a node) when their connection comes and goes. The same for the viewer of
this very node (it connects to /remote on loopback) and for the viewers of a paired client (they connect, through the
client, to the mutual-TLS listener): either way they end up registered in the WebviewManager, so that a playlist can be
assigned to their windows.
"""
from __future__ import annotations

import json
from typing import Optional

from loguru import logger


def _vm():
    from webview.controller import WebviewManager
    try:
        return WebviewManager.get_instance()
    except RuntimeError:  # still starting
        return None


async def _publish(vm):
    from api.ws.endpoints import WS
    await WS.broadcast('Viewers/list', viewers=vm.get_webviews(), assignments=vm.get_assignments())


async def viewer_connected(instance_id: str, ip: str, port: int, hostname: str):
    vm = _vm()
    if vm is None:
        return
    vm.register_webview(instance_id, {'ip': ip, 'port': port, 'hostname': hostname, 'screens': [], 'windows': []})
    await _publish(vm)


async def viewer_message(instance_id: str, raw: str) -> bool:
    """A frame from a viewer. True if it was understood (today: its screens and windows)."""
    try:
        msg = json.loads(raw)
        target = msg.pop('target', None)
    except (json.JSONDecodeError, AttributeError):
        logger.debug('Non-JSON message from viewer: {}', raw[:200])
        return False
    vm = _vm()
    if target == 'WebviewInfo' and vm:
        vm.update_webview_info(instance_id, msg)
        await _publish(vm)
        return True
    return False


async def viewer_gone(instance_id: str):
    vm = _vm()
    if vm:
        vm.unregister_webview(instance_id)
        await _publish(vm)


EMPTY_INFO = json.dumps({'target': 'WebviewInfo', 'screens': [], 'windows': []})


def parse_port(value: Optional[str], default: int) -> int:
    """A port taken from a header the other party sent: never trust it to be a number"""
    try:
        port = int(value)
        return port if 0 < port < 65536 else default
    except (TypeError, ValueError):
        return default
