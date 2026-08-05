from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from utils.models.assets import AssetsManager
from utils.models.command_line import cmdargs

if TYPE_CHECKING:
    from api.ws.wsmanager import WSManager


class PlaylistLoop:
    """Runs the playback loop for a single playlist and sends assets to assigned webviews"""

    def __init__(self, playlist: AssetsManager, remote_ws: WSManager):
        self.playlist = playlist
        self.remote_ws = remote_ws
        self.assigned_webviews: set[str] = set()
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if self._task is None or self._task.done():
            # get_event_loop().create_task also works at startup, when the
            # loop is already set but not yet running in the asyncio thread
            self._task = asyncio.get_event_loop().create_task(
                self._run(), name=f'playlist_{self.playlist.playlist_id}'
            )

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    def assign(self, webview_id: str):
        self.assigned_webviews.add(webview_id)

    def unassign(self, webview_id: str):
        self.assigned_webviews.discard(webview_id)

    async def _run(self):
        logger.info('Starting playlist loop: {} ({})', self.playlist.name, self.playlist.playlist_id)
        try:
            async for asset in self.playlist.iter_wait():
                if SHUTDOWN_EVENT.is_set():
                    break
                if not self.assigned_webviews:
                    continue
                url = asset.url
                if url.startswith('file:'):
                    url = 'https://localhost/uploaded/' + url.removeprefix('file:')
                data = dict(
                    src=url,
                    # -1 (undefined) is passed through as "unknown": the
                    # viewer then probes the URL itself and picks a container,
                    # which is how this worked before the Qt/CEF migration and
                    # is the only way a plain URL pointing straight at an image
                    # or a video can land anywhere but an iframe.
                    container=asset.media_type + 1 if asset.media_type >= 0 else -1,
                    fit=asset.fit,
                    bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else 'rgb(0,0,0)'
                )
                for webview_id in list(self.assigned_webviews):
                    await self.remote_ws.multicast(webview_id, 'Show', **data)
        except asyncio.CancelledError:
            logger.info('Playlist loop stopped: {}', self.playlist.playlist_id)

    async def send_current(self, webview_id: str):
        """Push the current asset of this playlist to a specific webview immediately"""
        asset = self.playlist.current
        url = asset.url
        if url.startswith('file:'):
            url = 'https://localhost/uploaded/' + url.removeprefix('file:')
        data = dict(
            src=url,
            container=asset.media_type + 1 if asset.media_type >= 0 else -1,
            fit=asset.fit,
            bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else 'rgb(0,0,0)'
        )
        await self.remote_ws.multicast(webview_id, 'Show', **data)


class WebviewManager:
    """
    Manages webview connections and their playlist assignments.

    Each webview is a Qt6+CEF instance identified by instance_id; a single
    UniTotem instance can have several webviews connected at the same time
    (the local one plus any remote client).
    Each playlist has an independent PlaylistLoop.
    Assignments map webview_id → playlist_id.
    """

    _instance: Optional[WebviewManager] = None

    def __init__(self, remote_ws: WSManager):
        self.remote_ws = remote_ws
        self._loops: dict[str, PlaylistLoop] = {}       # playlist_id → loop
        self._webviews: dict[str, dict] = {}             # instance_id → info dict
        self._assignments: dict[str, str] = {}          # instance_id → playlist_id
        self._pending_save: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> WebviewManager:
        if cls._instance is None:
            raise RuntimeError("WebviewManager not initialized yet")
        return cls._instance

    @classmethod
    def init(cls, remote_ws: WSManager) -> WebviewManager:
        cls._instance = cls(remote_ws)
        return cls._instance

    # ── Playlist loop management ──────────────────────────────────────────

    def add_playlist_loop(self, playlist: AssetsManager):
        loop = PlaylistLoop(playlist, self.remote_ws)
        self._loops[playlist.playlist_id] = loop
        loop.start()

    def remove_playlist_loop(self, playlist_id: str):
        loop = self._loops.pop(playlist_id, None)
        if loop:
            loop.stop()
            # Unassign all webviews assigned to this playlist
            for vid in [v for v, pid in self._assignments.items() if pid == playlist_id]:
                del self._assignments[vid]
            self._save_assignments()

    # ── Webview registration ───────────────────────────────────────────────

    def register_webview(self, instance_id: str, info: dict):
        logger.info('Webview connected: {} ({})', info.get('hostname', instance_id), instance_id)
        self._webviews[instance_id] = info
        # Restore what this viewer was already assigned to, whether that comes
        # from a previous run (loaded from disk at startup) or from it having
        # just dropped the connection. Without this a reboot leaves every
        # screen on the boot logo until somebody reassigns it by hand.
        playlist_id = self._assignments.get(instance_id)
        if playlist_id and playlist_id in self._loops:
            logger.info('Restoring assignment of {} to playlist {}', instance_id, playlist_id)
            self._loops[playlist_id].assign(instance_id)
            asyncio.create_task(self._loops[playlist_id].send_current(instance_id))

    def update_webview_info(self, instance_id: str, info: dict):
        if instance_id in self._webviews:
            self._webviews[instance_id].update(info)
        else:
            self._webviews[instance_id] = info

    def unregister_webview(self, instance_id: str):
        logger.info('Webview disconnected: {}', instance_id)
        self._webviews.pop(instance_id, None)
        # Detach from the loop so it stops sending assets to something that is
        # no longer there, but KEEP the assignment: a viewer that disconnects
        # has not been unassigned, it is merely absent (restarted, rebooted,
        # network blip), and it must resume its playlist when it comes back.
        playlist_id = self._assignments.get(instance_id)
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(instance_id)

    # ── Assignment persistence ────────────────────────────────────────────

    @staticmethod
    def _assignments_path() -> Path:
        """Kept beside the playlists it refers to, not inside playlists.json:
        that file is rewritten by every playlist operation, and which screen
        shows what is viewer state rather than playlist state."""
        return Path(cmdargs.assets_file).parent / 'viewer_assignments.json'

    def _save_assignments(self):
        """Write the viewer -> playlist map so it survives a restart.

        Playback on a totem must come back on its own after a reboot; needing
        someone to reopen the Viewers page and reassign every screen by hand is
        not an option for a device hanging on a wall.
        """
        path = self._assignments_path()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as f:
                json.dump(self._assignments, f, indent=4)
            tmp.replace(path)   # atomic: a power cut cannot leave a half file
        except OSError as exc:
            logger.error('Cannot save viewer assignments to {}: {}', path, exc)

    def load_assignments(self):
        """Read back the viewer -> playlist map saved by _save_assignments().

        Called at startup, before any viewer connects: the assignments simply
        sit here until the matching viewer registers, at which point
        register_webview() attaches it to its loop. Assignments pointing at a
        playlist that no longer exists are dropped.
        """
        path = self._assignments_path()
        if not path.exists():
            return
        try:
            with open(path) as f:
                stored = json.load(f)
        except (OSError, ValueError) as exc:
            logger.error('Cannot read viewer assignments from {}: {}', path, exc)
            return
        if not isinstance(stored, dict):
            logger.error('Ignoring malformed viewer assignments in {}', path)
            return
        for webview_id, playlist_id in stored.items():
            if playlist_id in self._loops:
                self._assignments[webview_id] = playlist_id
            else:
                logger.warning('Dropping assignment of {} to unknown playlist {}',
                               webview_id, playlist_id)
        logger.info('Restored {} viewer assignment(s)', len(self._assignments))

    # ── Assignment management ─────────────────────────────────────────────

    def assign(self, webview_id: str, playlist_id: str):
        # Remove from previous assignment
        old_pid = self._assignments.get(webview_id)
        if old_pid and old_pid in self._loops:
            self._loops[old_pid].unassign(webview_id)

        self._assignments[webview_id] = playlist_id
        self._save_assignments()
        if playlist_id in self._loops:
            self._loops[playlist_id].assign(webview_id)
            # Push current asset immediately to the newly assigned webview
            asyncio.create_task(self._loops[playlist_id].send_current(webview_id))

    def unassign(self, webview_id: str):
        playlist_id = self._assignments.pop(webview_id, None)
        self._save_assignments()
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(webview_id)

    def get_assignment(self, webview_id: str) -> Optional[str]:
        return self._assignments.get(webview_id)

    # ── Display / window control ──────────────────────────────────────────

    async def send_webview_command(self, webview_id: str, target: str, **kwargs):
        await self.remote_ws.multicast(webview_id, target, nocache=True, **kwargs)

    async def set_bounds(self, webview_id: str, window_id: int, x: int, y: int, width: int, height: int):
        if webview_id in self._webviews:
            windows = self._webviews[webview_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id].update({'x': x, 'y': y, 'width': width, 'height': height})
        await self.send_webview_command(webview_id, 'SetBounds',
                                       window_id=window_id, x=x, y=y, width=width, height=height)

    async def set_orientation(self, webview_id: str, window_id: int, orientation: int):
        if webview_id in self._webviews:
            windows = self._webviews[webview_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id]['orientation'] = orientation
        await self.send_webview_command(webview_id, 'SetOrientation',
                                       window_id=window_id, orientation=orientation)

    async def set_flip(self, webview_id: str, window_id: int, flip: int):
        if webview_id in self._webviews:
            windows = self._webviews[webview_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id]['flip'] = flip
        await self.send_webview_command(webview_id, 'SetFlip', window_id=window_id, flip=flip)

    async def add_window(self, webview_id: str, display: int = 0, x: int = 0, y: int = 0,
                         width: int = 1920, height: int = 1080):
        await self.send_webview_command(webview_id, 'AddWindow',
                                       display=display, x=x, y=y, width=width, height=height)

    async def remove_window(self, webview_id: str, window_id: int):
        await self.send_webview_command(webview_id, 'RemoveWindow', window_id=window_id)

    # ── Queries ───────────────────────────────────────────────────────────

    def get_webviews(self) -> dict[str, dict]:
        return {
            vid: {
                **info,
                'assigned_playlist': self._assignments.get(vid),
            }
            for vid, info in self._webviews.items()
        }

    def get_webview_screens(self, webview_id: str) -> list:
        return self._webviews.get(webview_id, {}).get('screens', [])

    def get_webview_windows(self, webview_id: str) -> list:
        return self._webviews.get(webview_id, {}).get('windows', [])

    def get_assignments(self) -> dict[str, str]:
        return dict(self._assignments)

    def serialize_assignments(self) -> list[dict]:
        return [
            {'webview_id': vid, 'playlist_id': pid}
            for vid, pid in self._assignments.items()
        ]
