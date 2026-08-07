from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from utils.models.assets import Asset, AssetsManager, idle_asset
from utils.models.command_line import cmdargs

if TYPE_CHECKING:
    from api.ws.wsmanager import WSManager


def show_payload(asset: Asset, window_id: int) -> dict:
    """The 'Show' command for one asset on one window.

    Built in a single place because the playback loop, the immediate push on
    assignment and the idle push for an unassigned window all need it, and when
    it was written out twice the copies drifted apart.
    """
    url = asset.url
    if url.startswith('file:'):
        url = 'https://localhost/uploaded/' + url.removeprefix('file:')
    return dict(
        src=url,
        window_id=window_id,
        # -1 (undefined) is passed through as "unknown": the viewer then
        # probes the URL itself and picks a container, which is how this
        # worked before the Qt/CEF migration and is the only way a plain
        # URL pointing straight at an image or a video can land anywhere
        # but an iframe.
        container=asset.media_type + 1 if asset.media_type >= 0 else -1,
        fit=asset.fit,
        # None means "no colour chosen", which the viewer needs to tell
        # apart from a deliberate black: only in the first case does it
        # sample the picture's own average colour for the letterbox bars.
        bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else None,
    )


class PlaylistLoop:
    """Runs the playback loop for a single playlist and sends assets to assigned webviews"""

    def __init__(self, playlist: AssetsManager, remote_ws: WSManager):
        self.playlist = playlist
        self.remote_ws = remote_ws
        # (webview_id, window_id): a totem drives one window per screen, and
        # each window gets its own playlist - two monitors on one node can show
        # different things. The same playlist may of course drive many windows.
        self.assigned_webviews: set[tuple[str, int]] = set()
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

    def assign(self, webview_id: str, window_id: int):
        self.assigned_webviews.add((webview_id, window_id))

    def unassign(self, webview_id: str, window_id: int):
        self.assigned_webviews.discard((webview_id, window_id))

    async def _run(self):
        logger.info('Starting playlist loop: {} ({})', self.playlist.name, self.playlist.playlist_id)
        try:
            async for asset in self.playlist.iter_wait():
                if SHUTDOWN_EVENT.is_set():
                    break
                if not self.assigned_webviews:
                    continue
                for webview_id, window_id in list(self.assigned_webviews):
                    await self.remote_ws.multicast(
                        webview_id, 'Show', **show_payload(asset, window_id))
        except asyncio.CancelledError:
            logger.info('Playlist loop stopped: {}', self.playlist.playlist_id)

    async def send_current(self, webview_id: str, window_id: int):
        """Push this playlist's current asset to one window straight away,
        so a freshly assigned screen does not sit on the boot logo until the
        next rotation."""
        await self.remote_ws.multicast(
            webview_id, 'Show', **show_payload(self.playlist.current, window_id))


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
        # (instance_id, window_id) → playlist_id. Keyed on the window, not on
        # the viewer: one screen is one window, and a node driving two screens
        # must be able to show a different playlist on each.
        self._assignments: dict[tuple[str, int], str] = {}
        self._pending_save: Optional[asyncio.Task] = None
        # Viewer → playlist entries read from the pre-per-window file format,
        # waiting for the viewer to connect so its windows can be resolved.
        self._legacy_assignments: dict[str, str] = {}
        # Windows already parked on the idle page, so _restore_assignments -
        # which runs on every info update - does not reload it under them each
        # time a screen is plugged or unplugged elsewhere on the same node.
        self._idle_windows: set[tuple[str, int]] = set()

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
            for key in [k for k, pid in self._assignments.items() if pid == playlist_id]:
                del self._assignments[key]
                # Deleting a playlist leaves its windows with nothing to show;
                # they must fall back to the idle page like any other
                # unassigned window rather than freeze on the last asset.
                if key not in self._idle_windows:
                    self._idle_windows.add(key)
                    asyncio.create_task(self._send_idle(*key))
            self._save_assignments()

    # ── Webview registration ───────────────────────────────────────────────

    def register_webview(self, instance_id: str, info: dict):
        logger.info('Webview connected: {} ({})', info.get('hostname', instance_id), instance_id)
        self._webviews[instance_id] = info
        # Restore what this viewer was already assigned to, whether that comes
        # from a previous run (loaded from disk at startup) or from it having
        # just dropped the connection. Without this a reboot leaves every
        # screen on the boot logo until somebody reassigns it by hand.
        self._restore_assignments(instance_id)

    async def _send_idle(self, instance_id: str, window_id: int):
        """Park one window on the idle page.

        A window with no playlist assigned would otherwise never be told
        anything at all, and would sit on boot-screen.html for as long as it
        exists - a spinner that means "loading" while nothing is loading, and
        (before the boot screen was made compositor-only) the single largest
        item in an idle node's CPU budget: 38% of a core at 1280x800, 178% at
        5120x2160, measured on the test VM.

        It shows the same page an assigned-but-empty playlist shows, welcome
        screen included where that still applies - see idle_asset().
        """
        await self.remote_ws.multicast(
            instance_id, 'Show', **show_payload(idle_asset(), window_id))

    def _restore_assignments(self, instance_id: str):
        """Attach a viewer's windows to the playlists they were assigned, and
        park the rest on the idle page.

        Deliberately driven by the window list rather than by connection:
        a viewer registers before it has reported its screens, so at that
        moment there is nothing to attach to. This runs again on every info
        update and is idempotent, which is also what makes a screen plugged in
        later pick its playlist back up - and, for a window that has no
        playlist, what gets it off the boot screen. This is the only place that
        sees the whole per-window assignment picture, which is why the idle
        push belongs here rather than in the viewer or in the page.
        """
        info = self._webviews.get(instance_id, {})
        window_ids = [w['window_id'] for w in info.get('windows', [])]
        if not window_ids:
            return

        legacy = self._legacy_assignments.pop(instance_id, None)
        if legacy is not None:
            # Only now are the windows known, so an entry saved before
            # assignments were per-window can be spread over all of them,
            # which is what it meant.
            for window_id in window_ids:
                self._assignments.setdefault((instance_id, window_id), legacy)
            self._save_assignments()

        for window_id in window_ids:
            playlist_id = self._assignments.get((instance_id, window_id))
            loop = self._loops.get(playlist_id) if playlist_id else None
            if loop:
                self._idle_windows.discard((instance_id, window_id))
                if (instance_id, window_id) not in loop.assigned_webviews:
                    logger.info('Restoring window {} of {} to playlist {}',
                                window_id, instance_id, playlist_id)
                    loop.assign(instance_id, window_id)
                    asyncio.create_task(loop.send_current(instance_id, window_id))
            elif (instance_id, window_id) not in self._idle_windows:
                logger.info('Window {} of {} has no playlist: showing the idle page',
                            window_id, instance_id)
                self._idle_windows.add((instance_id, window_id))
                asyncio.create_task(self._send_idle(instance_id, window_id))

    def update_webview_info(self, instance_id: str, info: dict):
        if instance_id in self._webviews:
            self._webviews[instance_id].update(info)
        else:
            self._webviews[instance_id] = info
        self._restore_assignments(instance_id)

    def unregister_webview(self, instance_id: str):
        logger.info('Webview disconnected: {}', instance_id)
        self._webviews.pop(instance_id, None)
        # A viewer that comes back has restarted its browser and is on the boot
        # screen again, so forget that its windows were ever parked - otherwise
        # they would be left spinning, with this side believing they are idle.
        self._idle_windows = {(vid, wid) for vid, wid in self._idle_windows
                              if vid != instance_id}
        # Detach from the loop so it stops sending assets to something that is
        # no longer there, but KEEP the assignment: a viewer that disconnects
        # has not been unassigned, it is merely absent (restarted, rebooted,
        # network blip), and it must resume its playlist when it comes back.
        for (vid, window_id), playlist_id in self._assignments.items():
            if vid == instance_id and playlist_id in self._loops:
                self._loops[playlist_id].unassign(vid, window_id)

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
                # A list of records rather than a mapping: the key is a pair,
                # which JSON cannot express as an object key.
                json.dump([{'webview_id': vid, 'window_id': wid, 'playlist_id': pid}
                           for (vid, wid), pid in self._assignments.items()], f, indent=4)
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
        if isinstance(stored, dict):
            # Format written before assignments became per-window: one playlist
            # for the whole viewer. Read as "every window of that viewer", which
            # is what it meant, so an already-updated node keeps playing.
            stored = [{'webview_id': vid, 'window_id': None, 'playlist_id': pid}
                      for vid, pid in stored.items()]
        if not isinstance(stored, list):
            logger.error('Ignoring malformed viewer assignments in {}', path)
            return
        for record in stored:
            try:
                webview_id  = record['webview_id']
                playlist_id = record['playlist_id']
                window_id   = record.get('window_id')
            except (TypeError, KeyError):
                logger.warning('Ignoring malformed assignment record: {}', record)
                continue
            if playlist_id not in self._loops:
                logger.warning('Dropping assignment of {} to unknown playlist {}',
                               webview_id, playlist_id)
                continue
            if window_id is None:
                # Legacy record: remembered separately and applied to whatever
                # windows the viewer turns out to have when it registers.
                self._legacy_assignments[webview_id] = playlist_id
            else:
                self._assignments[(webview_id, window_id)] = playlist_id
        logger.info('Restored {} viewer assignment(s)', len(self._assignments))

    # ── Assignment management ─────────────────────────────────────────────

    def assign(self, webview_id: str, window_id: int, playlist_id: str):
        # A window shows one playlist at a time, so detach it from the previous
        # one first. The reverse is not true: a playlist may drive any number
        # of windows.
        old_pid = self._assignments.get((webview_id, window_id))
        if old_pid and old_pid in self._loops:
            self._loops[old_pid].unassign(webview_id, window_id)

        self._assignments[(webview_id, window_id)] = playlist_id
        self._save_assignments()
        if playlist_id in self._loops:
            self._idle_windows.discard((webview_id, window_id))
            self._loops[playlist_id].assign(webview_id, window_id)
            # Push the current asset immediately, so the window does not sit on
            # the boot logo until the playlist happens to rotate
            asyncio.create_task(self._loops[playlist_id].send_current(webview_id, window_id))

    def unassign(self, webview_id: str, window_id: int):
        playlist_id = self._assignments.pop((webview_id, window_id), None)
        self._save_assignments()
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(webview_id, window_id)
        # The window keeps showing whatever the playlist last gave it unless it
        # is told otherwise, so send it back to the idle page explicitly.
        if (webview_id, window_id) not in self._idle_windows:
            self._idle_windows.add((webview_id, window_id))
            asyncio.create_task(self._send_idle(webview_id, window_id))

    def get_assignment(self, webview_id: str, window_id: int) -> Optional[str]:
        return self._assignments.get((webview_id, window_id))

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
        """Every connected viewer, with each of its windows carrying the
        playlist assigned to that window - the web UI draws one column per
        window, not per viewer."""
        return {
            vid: {
                **info,
                'windows': [
                    {**w, 'assigned_playlist': self._assignments.get((vid, w['window_id']))}
                    for w in info.get('windows', [])
                ],
            }
            for vid, info in self._webviews.items()
        }

    def get_webview_screens(self, webview_id: str) -> list:
        return self._webviews.get(webview_id, {}).get('screens', [])

    def get_webview_windows(self, webview_id: str) -> list:
        return self._webviews.get(webview_id, {}).get('windows', [])

    def get_assignments(self) -> list[dict]:
        """Flat list, since the key is a (viewer, window) pair and JSON has no
        way to express that as an object key."""
        return self.serialize_assignments()

    def serialize_assignments(self) -> list[dict]:
        return [
            {'webview_id': vid, 'window_id': wid, 'playlist_id': pid}
            for (vid, wid), pid in self._assignments.items()
        ]
