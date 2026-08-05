import asyncio
from typing import Optional

import aiohttp

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.models.viewer import viewer_settings
from webview.controller import WebviewManager

# Must match webview.app.CDP_PORT. Duplicated as a plain literal (rather than
# imported) because webview.app hard-imports PySide6/cefpython at module
# level and this module must stay importable on headless nodes without them.
_CDP_PORT = 9223


def _vm() -> WebviewManager:
    # deferred: WebviewManager is initialized in main.py, after this module
    # has already been imported and instantiated by the WebSocketAPI registry
    return WebviewManager.get_instance()


class Display(WSAPIBase):

    def getDisplays(self, viewer_id: Optional[str] = None):
        if viewer_id:
            return WSBroadcast(displays=_vm().get_webview_screens(viewer_id))
        # Return screens grouped by webview
        return WSBroadcast(displays={
            vid: _vm().get_webview_screens(vid)
            for vid in _vm().get_webviews()
        })

    async def getGPUFeatureStats(self):
        """
        Graphics Feature Status for this node's own local webview - the same
        data chrome://gpu and Electron's old app.getGPUFeatureStatus() both
        surface (both are just views onto Chromium's GPU feature status
        service). Queried via the Chrome DevTools Protocol's SystemInfo.getInfo
        (a stable, documented CDP method) against the local CEF instance's
        loopback-only debugging port, since cefpython itself exposes no
        equivalent API. Empty dict if no local webview is running (headless
        node, or webview not started yet) or the query fails for any reason.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f'http://127.0.0.1:{_CDP_PORT}/json/version',
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    ws_url = (await resp.json())['webSocketDebuggerUrl']
                async with session.ws_connect(ws_url, timeout=2) as ws:
                    await ws.send_json({'id': 1, 'method': 'SystemInfo.getInfo'})
                    async for msg in ws:
                        data = msg.json()
                        if data.get('id') == 1:
                            return WSBroadcast(
                                features=data.get('result', {}).get('gpu', {}).get('featureStatus', {})
                            )
        except (aiohttp.ClientError, OSError, KeyError, asyncio.TimeoutError):
            pass
        return WSBroadcast(features={})

    def getBounds(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_webview_windows(viewer_id)
        if window_id < len(windows):
            return WSBroadcast(**windows[window_id])
        return WSBroadcast()

    def setBounds(self, viewer_id: str, window_id: int, x: int, y: int, width: int, height: int):
        asyncio.create_task(_vm().set_bounds(viewer_id, window_id, x, y, width, height))
        return WSBroadcast(x=x, y=y, width=width, height=height)

    def getOrientation(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_webview_windows(viewer_id)
        orientation = windows[window_id].get('orientation', 0) if window_id < len(windows) else 0
        return WSBroadcast(orientation=orientation)

    def setOrientation(self, viewer_id: str, window_id: int, orientation: int):
        asyncio.create_task(_vm().set_orientation(viewer_id, window_id, orientation))
        return WSBroadcast(orientation=orientation)

    def getFlip(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_webview_windows(viewer_id)
        flip = windows[window_id].get('flip', 0) if window_id < len(windows) else 0
        return WSBroadcast(flip=flip)

    def setFlip(self, viewer_id: str, window_id: int, flip: int):
        asyncio.create_task(_vm().set_flip(viewer_id, window_id, flip))
        return WSBroadcast(flip=flip)

    def addWindow(self, viewer_id: str, display: int = 0, x: int = 0, y: int = 0,
                  width: int = 1920, height: int = 1080):
        asyncio.create_task(_vm().add_window(viewer_id, display, x, y, width, height))
        return WSBroadcast()

    def removeWindow(self, viewer_id: str, window_id: int):
        asyncio.create_task(_vm().remove_window(viewer_id, window_id))
        return WSBroadcast()

    def getAllowInsecureCerts(self):
        """Whether the viewer displays assets whose certificate does not
        validate. Admin-only, like every other method here (a method with no
        explicit permission requires admin - see api.ws.permissions)."""
        return WSBroadcast(allow=viewer_settings.allow_insecure_certs)

    def setAllowInsecureCerts(self, allow: bool):
        """Stored now, applied by the viewer the next time it starts: CEF
        takes it as a command-line switch at initialisation and Chromium
        offers no way to change it on a running browser."""
        viewer_settings.allow_insecure_certs = allow
        viewer_settings.save()
        return WSBroadcast(self.getAllowInsecureCerts, allow=viewer_settings.allow_insecure_certs)
