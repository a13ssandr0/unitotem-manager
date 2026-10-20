"""
What the "screen of the totem" window needs to ask before it opens the stream (the stream itself is the /vnc websocket,
see unitotem_node/vnc.py). Path: Vnc/<function>.
"""
from api.ws.responses import WSBroadcast
from api.ws.wsmanager import WSAPIBase
from unitotem_node.system import vnc
from unitotem_node.system.screens import wayland
from utils.models.user import UserPerms


class Vnc(WSAPIBase):
    @staticmethod
    @UserPerms.requires.admin
    async def outputs():
        """The outputs that can be watched on their own, and whether the VNC server is installed at all."""
        return WSBroadcast(
            outputs=[{'name': o.name, 'width': o.width, 'height': o.height, 'x': o.x, 'y': o.y}
                     for o in await wayland.outputs() if o.active],
            available=vnc.find_binary() is not None)
