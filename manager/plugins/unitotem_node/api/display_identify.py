"""
Identify a screen of this node: its output name and screen number are shown large on its window for a few seconds
(the local viewer draws them, see `Identify` in docs/manager/viewer.md).

Paths: Settings/Display/Screens/identify. Bound as a base of Screens (unitotem_node.api.display_screens), so it sits with the
other screen calls and needs the same permission as they do.
"""
import re
from typing import Optional

from api.ws.responses import WSResponse
from utils.models.user import UserPerms
from webview.controller import WebviewManager

# How the viewer of this very node is known to its own manager (utils/remote/bundle.py)
LOCAL_VIEWER = 'local-webview'
# An output name as the compositor reports it (HDMI-A-1, DP-2, Virtual-1)
_NAME = re.compile(r'[A-Za-z0-9._-]{1,64}')


class IdentifyScreen:
    @UserPerms.requires.power
    async def identify(self, name: Optional[str] = None):
        """Shows the name and the number of a screen on its window for a few seconds. Without a name, every screen shows its own."""
        manager = WebviewManager.get_instance()
        if name is not None:
            if not _NAME.fullmatch(name):
                return WSResponse(error=f'Not a screen name: {name!r}')
            if name not in [screen.get('name') for screen in manager.get_webview_screens(LOCAL_VIEWER)]:
                return WSResponse(error=f'No screen named {name}')
        await manager.send_webview_command(LOCAL_VIEWER, 'Identify', screen_name=name)
        return None
