from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from utils.models.assets import AssetsManager

if TYPE_CHECKING:
    from api.ws.wsmanager import WSManager


class PlaylistLoop:
    """Runs the playback loop for a single playlist and sends assets to assigned viewers"""

    def __init__(self, playlist: AssetsManager, remote_ws: WSManager):
        self.playlist = playlist
        self.remote_ws = remote_ws
        self.assigned_viewers: set[str] = set()
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

    def assign(self, viewer_id: str):
        self.assigned_viewers.add(viewer_id)

    def unassign(self, viewer_id: str):
        self.assigned_viewers.discard(viewer_id)

    async def _run(self):
        logger.info('Starting playlist loop: {} ({})', self.playlist.name, self.playlist.playlist_id)
        try:
            async for asset in self.playlist.iter_wait():
                if SHUTDOWN_EVENT.is_set():
                    break
                if not self.assigned_viewers:
                    continue
                url = asset.url
                if url.startswith('file:'):
                    url = 'https://localhost/uploaded/' + url.removeprefix('file:')
                data = dict(
                    src=url,
                    container=asset.media_type + 1,
                    fit=asset.fit,
                    bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else 'rgb(0,0,0)'
                )
                for viewer_id in list(self.assigned_viewers):
                    await self.remote_ws.multicast(viewer_id, 'Show', **data)
        except asyncio.CancelledError:
            logger.info('Playlist loop stopped: {}', self.playlist.playlist_id)

    async def send_current(self, viewer_id: str):
        """Push the current asset of this playlist to a specific viewer immediately"""
        asset = self.playlist.current
        url = asset.url
        if url.startswith('file:'):
            url = 'https://localhost/uploaded/' + url.removeprefix('file:')
        data = dict(
            src=url,
            container=asset.media_type + 1,
            fit=asset.fit,
            bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else 'rgb(0,0,0)'
        )
        await self.remote_ws.multicast(viewer_id, 'Show', **data)


class ViewerManager:
    """
    Manages viewer connections and their playlist assignments.

    Each viewer is a Qt6+CEF process identified by instance_id.
    Each playlist has an independent PlaylistLoop.
    Assignments map viewer_id → playlist_id.
    """

    _instance: Optional[ViewerManager] = None

    def __init__(self, remote_ws: WSManager):
        self.remote_ws = remote_ws
        self._loops: dict[str, PlaylistLoop] = {}       # playlist_id → loop
        self._viewers: dict[str, dict] = {}             # instance_id → info dict
        self._assignments: dict[str, str] = {}          # instance_id → playlist_id
        self._pending_save: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> ViewerManager:
        if cls._instance is None:
            raise RuntimeError("ViewerManager not initialized yet")
        return cls._instance

    @classmethod
    def init(cls, remote_ws: WSManager) -> ViewerManager:
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
            # Unassign all viewers assigned to this playlist
            for vid in [v for v, pid in self._assignments.items() if pid == playlist_id]:
                del self._assignments[vid]

    # ── Viewer registration ───────────────────────────────────────────────

    def register_viewer(self, instance_id: str, info: dict):
        logger.info('Viewer connected: {} ({})', info.get('hostname', instance_id), instance_id)
        self._viewers[instance_id] = info

    def update_viewer_info(self, instance_id: str, info: dict):
        if instance_id in self._viewers:
            self._viewers[instance_id].update(info)
        else:
            self._viewers[instance_id] = info

    def unregister_viewer(self, instance_id: str):
        logger.info('Viewer disconnected: {}', instance_id)
        self._viewers.pop(instance_id, None)
        # Remove from any assigned loop
        playlist_id = self._assignments.pop(instance_id, None)
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(instance_id)

    # ── Assignment management ─────────────────────────────────────────────

    def assign(self, viewer_id: str, playlist_id: str):
        # Remove from previous assignment
        old_pid = self._assignments.get(viewer_id)
        if old_pid and old_pid in self._loops:
            self._loops[old_pid].unassign(viewer_id)

        self._assignments[viewer_id] = playlist_id
        if playlist_id in self._loops:
            self._loops[playlist_id].assign(viewer_id)
            # Push current asset immediately to the newly assigned viewer
            asyncio.create_task(self._loops[playlist_id].send_current(viewer_id))

    def unassign(self, viewer_id: str):
        playlist_id = self._assignments.pop(viewer_id, None)
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(viewer_id)

    def get_assignment(self, viewer_id: str) -> Optional[str]:
        return self._assignments.get(viewer_id)

    # ── Display / window control ──────────────────────────────────────────

    async def send_viewer_command(self, viewer_id: str, target: str, **kwargs):
        await self.remote_ws.multicast(viewer_id, target, nocache=True, **kwargs)

    async def set_bounds(self, viewer_id: str, window_id: int, x: int, y: int, width: int, height: int):
        if viewer_id in self._viewers:
            windows = self._viewers[viewer_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id].update({'x': x, 'y': y, 'width': width, 'height': height})
        await self.send_viewer_command(viewer_id, 'SetBounds',
                                       window_id=window_id, x=x, y=y, width=width, height=height)

    async def set_orientation(self, viewer_id: str, window_id: int, orientation: int):
        if viewer_id in self._viewers:
            windows = self._viewers[viewer_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id]['orientation'] = orientation
        await self.send_viewer_command(viewer_id, 'SetOrientation',
                                       window_id=window_id, orientation=orientation)

    async def set_flip(self, viewer_id: str, window_id: int, flip: int):
        if viewer_id in self._viewers:
            windows = self._viewers[viewer_id].get('windows', [])
            if window_id < len(windows):
                windows[window_id]['flip'] = flip
        await self.send_viewer_command(viewer_id, 'SetFlip', window_id=window_id, flip=flip)

    async def add_window(self, viewer_id: str, display: int = 0, x: int = 0, y: int = 0,
                         width: int = 1920, height: int = 1080):
        await self.send_viewer_command(viewer_id, 'AddWindow',
                                       display=display, x=x, y=y, width=width, height=height)

    async def remove_window(self, viewer_id: str, window_id: int):
        await self.send_viewer_command(viewer_id, 'RemoveWindow', window_id=window_id)

    # ── Queries ───────────────────────────────────────────────────────────

    def get_viewers(self) -> dict[str, dict]:
        return {
            vid: {
                **info,
                'assigned_playlist': self._assignments.get(vid),
            }
            for vid, info in self._viewers.items()
        }

    def get_viewer_screens(self, viewer_id: str) -> list:
        return self._viewers.get(viewer_id, {}).get('screens', [])

    def get_viewer_windows(self, viewer_id: str) -> list:
        return self._viewers.get(viewer_id, {}).get('windows', [])

    def get_assignments(self) -> dict[str, str]:
        return dict(self._assignments)

    def serialize_assignments(self) -> list[dict]:
        return [
            {'viewer_id': vid, 'playlist_id': pid}
            for vid, pid in self._assignments.items()
        ]
