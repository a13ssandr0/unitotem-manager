import asyncio
from typing import Optional

import aiohttp

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast
from utils.models.user import UserPerms
from utils.models.viewer import viewer_settings
from utils.system.gpu import detect_gpu
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

    def displays(self, viewer_id: Optional[str] = None):
        if viewer_id:
            return WSBroadcast(displays=_vm().get_webview_screens(viewer_id))
        # Return screens grouped by webview
        return WSBroadcast(displays={
            vid: _vm().get_webview_screens(vid)
            for vid in _vm().get_webviews()
        })

    async def gpu_feature_stats(self):
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
                            gpu = data.get('result', {}).get('gpu', {})
                            return WSBroadcast(
                                features=gpu.get('featureStatus', {}),
                                # What Chromium reports as feature status only
                                # says whether a feature is blocklisted, not
                                # whether anything is actually offloaded -
                                # video_decode reads "enabled" on a machine
                                # with no accelerated decode profiles at all.
                                # These two go with it: the decode profiles the
                                # GPU process really advertises, and which
                                # switch profile this node was started with.
                                video_decoding=gpu.get('videoDecoding', []),
                                gpu_profile=detect_gpu().as_dict(),
                            )
        except (aiohttp.ClientError, OSError, KeyError, asyncio.TimeoutError):
            pass
        return WSBroadcast(features={}, video_decoding=[],
                           gpu_profile=detect_gpu().as_dict())

    @UserPerms.requires.none
    def bounds(self, viewer_id: str, window_id: int = 0):
        """Size of a window, shown in the navbar of every page."""
        windows = _vm().get_webview_windows(viewer_id)
        if window_id < len(windows):
            return WSBroadcast(**windows[window_id])
        return WSBroadcast()

    def set_bounds(self, viewer_id: str, window_id: int, x: int, y: int, width: int, height: int):
        asyncio.create_task(_vm().set_bounds(viewer_id, window_id, x, y, width, height))
        return WSBroadcast(self.bounds, x=x, y=y, width=width, height=height)

    def orientation(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_webview_windows(viewer_id)
        orientation = windows[window_id].get('orientation', 0) if window_id < len(windows) else 0
        return WSBroadcast(orientation=orientation)

    def set_orientation(self, viewer_id: str, window_id: int, orientation: int):
        asyncio.create_task(_vm().set_orientation(viewer_id, window_id, orientation))
        return WSBroadcast(self.orientation, orientation=orientation)

    def flip(self, viewer_id: str, window_id: int = 0):
        windows = _vm().get_webview_windows(viewer_id)
        flip = windows[window_id].get('flip', 0) if window_id < len(windows) else 0
        return WSBroadcast(flip=flip)

    def set_flip(self, viewer_id: str, window_id: int, flip: int):
        asyncio.create_task(_vm().set_flip(viewer_id, window_id, flip))
        return WSBroadcast(self.flip, flip=flip)

    def allow_insecure_certs(self):
        """Whether the viewer displays assets whose certificate does not
        validate. Admin-only, like every other method here (a method with no
        explicit permission requires admin - see api.ws.permissions)."""
        return WSBroadcast(allow=viewer_settings.allow_insecure_certs)

    def set_allow_insecure_certs(self, allow: bool):
        """Stored now, applied by the viewer the next time it starts: CEF
        takes it as a command-line switch at initialisation and Chromium
        offers no way to change it on a running browser."""
        viewer_settings.allow_insecure_certs = allow
        viewer_settings.save()
        return WSBroadcast(self.allow_insecure_certs, allow=viewer_settings.allow_insecure_certs)
