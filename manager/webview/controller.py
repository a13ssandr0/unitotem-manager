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
        # (instance_id, screen_name) → playlist_id. Keyed on the screen, not on
        # the viewer: one screen is one window, and a node driving two screens
        # must be able to show a different playlist on each.
        #
        # The key is the RandR output name ('HDMI-1', 'DP-1') rather than the
        # window_id it used to be, because window_id is minted by app.py's
        # _next_id and never reused: an output that goes away and comes back -
        # which is exactly what screen power control does with
        # `xrandr --output X --off/--auto` - returns under a NEW window_id and
        # orphaned its assignment. The spec requires the opposite ("the
        # playlist outputting to that window must be unaware of the change").
        # The output name survives that cycle; the window_id does not.
        self._assignments: dict[tuple[str, str], str] = {}
        self._pending_save: Optional[asyncio.Task] = None
        # Viewer → playlist entries read from the pre-per-window file format,
        # waiting for the viewer to connect so its windows can be resolved.
        self._legacy_assignments: dict[str, str] = {}
        # instance_id → {window_id: playlist_id}, read from the window_id-keyed
        # file format. Resolved to screen names the first time the viewer
        # reports its windows, since only then is the mapping knowable.
        self._pending_window_assignments: dict[str, dict[int, str]] = {}
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
            for vid, screen_name in [k for k, pid in self._assignments.items() if pid == playlist_id]:
                del self._assignments[(vid, screen_name)]
                # Deleting a playlist leaves its windows with nothing to show;
                # they must fall back to the idle page like any other
                # unassigned window rather than freeze on the last asset.
                # Only a screen that currently has a window can be told; one
                # that is off or unplugged gets the idle page from
                # _restore_assignments when it comes back.
                window_id = self._window_of(vid, screen_name)
                if window_id is not None and (vid, window_id) not in self._idle_windows:
                    self._idle_windows.add((vid, window_id))
                    asyncio.create_task(self._send_idle(vid, window_id))
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

    def _windows_of(self, instance_id: str) -> list[tuple[int, Optional[str]]]:
        """(window_id, screen_name) for every window a viewer has reported.

        Both halves are needed everywhere: assignments are keyed on the screen
        name, while the command channel and the playlist loops still address a
        window by its window_id (see _assignments). This is the single place
        that reads the pairing out of the WebviewInfo payload.
        """
        return [(w['window_id'], w.get('screen_name'))
                for w in self._webviews.get(instance_id, {}).get('windows', [])]

    def _screen_of(self, instance_id: str, window_id: int) -> Optional[str]:
        """The output a window sits on, or None if the viewer never said."""
        for wid, screen_name in self._windows_of(instance_id):
            if wid == window_id:
                return screen_name
        return None

    def _window_of(self, instance_id: str, screen_name: str) -> Optional[int]:
        """The window currently on an output, or None if there is none."""
        for wid, name in self._windows_of(instance_id):
            if name == screen_name:
                return wid
        return None

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

        It is also where the two older on-disk formats are migrated, for the
        same reason: a window_id or a whole viewer can only be resolved to an
        output name once the viewer has said which windows are on which screen.
        """
        windows = self._windows_of(instance_id)
        if not windows:
            return

        # Forget windows this viewer no longer has. Closing a window never
        # detached it from its playlist loop, so the loop went on multicasting
        # Show to a window_id nothing answers to; harmless (the viewer drops a
        # command for an unknown window) but unbounded, and screen power
        # control turns an off/on cycle into an everyday operation rather than
        # a rare one. window_ids are never reused, so a stale entry can never
        # be mistaken for a live one.
        live_ids = {window_id for window_id, _ in windows}
        for loop in self._loops.values():
            for vid, window_id in [k for k in loop.assigned_webviews
                                   if k[0] == instance_id and k[1] not in live_ids]:
                logger.info('Detaching gone window {} of {} from playlist {}',
                            window_id, vid, loop.playlist.playlist_id)
                loop.unassign(vid, window_id)
        self._idle_windows -= {(vid, window_id) for vid, window_id in self._idle_windows
                               if vid == instance_id and window_id not in live_ids}

        legacy = self._legacy_assignments.pop(instance_id, None)
        if legacy is not None:
            # Only now are the windows known, so an entry saved before
            # assignments were per-window can be spread over all of them,
            # which is what it meant.
            for _window_id, screen_name in windows:
                if screen_name is not None:
                    self._assignments.setdefault((instance_id, screen_name), legacy)
            self._save_assignments()

        pending = self._pending_window_assignments.get(instance_id)
        if pending:
            # Records saved when assignments were keyed on window_id. The
            # mapping is unknowable at load time (no viewer is connected yet),
            # so it is resolved here, against the windows the viewer reports.
            #
            # A record whose window does not exist right now is KEPT pending
            # rather than dropped: window numbering restarts from 0 with the
            # viewer, so a screen that is merely unplugged at this moment will
            # very likely take that same number when it is plugged back in, and
            # dropping it would silently unassign a monitor that was only
            # temporarily absent. The file is left in its old format until
            # every record has found an output, so a restart in the meantime
            # re-reads them intact instead of finding them rewritten away.
            for window_id, playlist_id in list(pending.items()):
                screen_name = self._screen_of(instance_id, window_id)
                if screen_name is None:
                    logger.info('Assignment of window {} of {} to {} stays pending: '
                                'no window with that number is on any named output yet',
                                window_id, instance_id, playlist_id)
                    continue
                current = self._assignments.get((instance_id, screen_name))
                if current is None:
                    self._assignments[(instance_id, screen_name)] = playlist_id
                elif current != playlist_id:
                    # Two records resolved onto the same output: window
                    # numbering is reused across viewer restarts, so an old
                    # record can land on a screen that has already been given a
                    # playlist by name. The named one wins, being the newer and
                    # unambiguous of the two - but say so, because from the
                    # outside it looks exactly like an assignment going missing.
                    logger.warning('Assignment of window {} of {} to {} discarded: '
                                   'that window turned out to be on {}, which is '
                                   'already assigned to {}',
                                   window_id, instance_id, playlist_id,
                                   screen_name, current)
                del pending[window_id]
            if not pending:
                del self._pending_window_assignments[instance_id]
            self._save_assignments()

        for window_id, screen_name in windows:
            playlist_id = self._assignments.get((instance_id, screen_name)) if screen_name else None
            loop = self._loops.get(playlist_id) if playlist_id else None
            if loop:
                self._idle_windows.discard((instance_id, window_id))
                if (instance_id, window_id) not in loop.assigned_webviews:
                    logger.info('Restoring window {} ({}) of {} to playlist {}',
                                window_id, screen_name, instance_id, playlist_id)
                    loop.assign(instance_id, window_id)
                    asyncio.create_task(loop.send_current(instance_id, window_id))
            elif (instance_id, window_id) not in self._idle_windows:
                logger.info('Window {} ({}) of {} has no playlist: showing the idle page',
                            window_id, screen_name, instance_id)
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
        # Driven off the loops rather than off _assignments because the window
        # list is gone by now (popped above), so there is nothing left to
        # resolve an output name against - and because it also clears windows
        # the assignments no longer mention.
        for loop in self._loops.values():
            for vid, window_id in [k for k in loop.assigned_webviews if k[0] == instance_id]:
                loop.unassign(vid, window_id)

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
                #
                # Assignments still waiting to be migrated are written back in
                # the format they came in, unresolved. They belong to a viewer
                # or a screen that has not shown up yet, and any save triggered
                # in the meantime - somebody assigning an unrelated screen from
                # the web UI - would otherwise erase them. Each record carries
                # its own format, and load_assignments() reads them per record.
                records = [{'webview_id': vid, 'screen_name': name, 'playlist_id': pid}
                           for (vid, name), pid in self._assignments.items()]
                records += [{'webview_id': vid, 'window_id': wid, 'playlist_id': pid}
                            for vid, windows in self._pending_window_assignments.items()
                            for wid, pid in windows.items()]
                records += [{'webview_id': vid, 'playlist_id': pid}
                            for vid, pid in self._legacy_assignments.items()]
                json.dump(records, f, indent=4)
            tmp.replace(path)   # atomic: a power cut cannot leave a half file
        except OSError as exc:
            logger.error('Cannot save viewer assignments to {}: {}', path, exc)

    def load_assignments(self):
        """Read back the viewer -> playlist map saved by _save_assignments().

        Called at startup, before any viewer connects: the assignments simply
        sit here until the matching viewer registers, at which point
        register_webview() attaches it to its loop. Assignments pointing at a
        playlist that no longer exists are dropped.

        Three on-disk formats are accepted, because a node being updated must
        keep playing rather than come back with every screen unassigned:
          1. a mapping viewer -> playlist, from before assignments were
             per-window;
          2. a list of {webview_id, window_id, playlist_id}, from when the key
             was the window;
          3. a list of {webview_id, screen_name, playlist_id}, the current one.
        Only the third can be applied here. The other two need the viewer to
        report which windows are on which output, so they are parked and
        resolved in _restore_assignments(), which then rewrites the file.
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
        pending = 0
        for record in stored:
            try:
                webview_id  = record['webview_id']
                playlist_id = record['playlist_id']
                screen_name = record.get('screen_name')
                window_id   = record.get('window_id')
            except (TypeError, KeyError):
                logger.warning('Ignoring malformed assignment record: {}', record)
                continue
            if playlist_id not in self._loops:
                logger.warning('Dropping assignment of {} to unknown playlist {}',
                               webview_id, playlist_id)
                continue
            if screen_name is not None:
                self._assignments[(webview_id, screen_name)] = playlist_id
            elif 'screen_name' in record:
                # Written for a window the viewer never named - _windows_of()
                # yields None for a window whose screen_name the WebviewInfo
                # payload omitted. Keying on None would make one bucket that
                # every unnamed window of that viewer shares; treating it as
                # the oldest format instead would spread it over every screen.
                # Neither is what it meant, so drop it, loudly.
                logger.warning('Dropping assignment of {} to {}: the record names '
                               'no output', webview_id, playlist_id)
            elif window_id is not None:
                # Keyed on the window: which output that was is unknowable
                # until the viewer reports its windows.
                self._pending_window_assignments.setdefault(webview_id, {})[window_id] = playlist_id
                pending += 1
            else:
                # Oldest format: one playlist for the whole viewer, applied to
                # whatever windows it turns out to have when it registers.
                self._legacy_assignments[webview_id] = playlist_id
                pending += 1
        logger.info('Restored {} viewer assignment(s), {} awaiting migration',
                    len(self._assignments), pending)

    # ── Assignment management ─────────────────────────────────────────────

    def assign(self, webview_id: str, window_id: int, playlist_id: str):
        # Addressed by window from the outside (that is what the web UI has to
        # hand) and stored by output name - see _assignments.
        screen_name = self._screen_of(webview_id, window_id)
        if screen_name is None:
            logger.warning('Refusing to assign window {} of {}: it is not on any '
                           'named output, so the assignment could not be restored',
                           window_id, webview_id)
            return

        # A window shows one playlist at a time, so detach it from the previous
        # one first. The reverse is not true: a playlist may drive any number
        # of windows.
        old_pid = self._assignments.get((webview_id, screen_name))
        if old_pid and old_pid in self._loops:
            self._loops[old_pid].unassign(webview_id, window_id)

        self._assignments[(webview_id, screen_name)] = playlist_id
        self._save_assignments()
        if playlist_id in self._loops:
            self._idle_windows.discard((webview_id, window_id))
            self._loops[playlist_id].assign(webview_id, window_id)
            # Push the current asset immediately, so the window does not sit on
            # the boot logo until the playlist happens to rotate
            asyncio.create_task(self._loops[playlist_id].send_current(webview_id, window_id))

    def unassign(self, webview_id: str, window_id: int):
        screen_name = self._screen_of(webview_id, window_id)
        playlist_id = self._assignments.pop((webview_id, screen_name), None) if screen_name else None
        self._save_assignments()
        if playlist_id and playlist_id in self._loops:
            self._loops[playlist_id].unassign(webview_id, window_id)
        # The window keeps showing whatever the playlist last gave it unless it
        # is told otherwise, so send it back to the idle page explicitly.
        if (webview_id, window_id) not in self._idle_windows:
            self._idle_windows.add((webview_id, window_id))
            asyncio.create_task(self._send_idle(webview_id, window_id))

    def get_assignment(self, webview_id: str, window_id: int) -> Optional[str]:
        screen_name = self._screen_of(webview_id, window_id)
        return self._assignments.get((webview_id, screen_name)) if screen_name else None

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
                    {**w, 'assigned_playlist': self._assignments.get((vid, w.get('screen_name')))}
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
        """Flat list, since the key is a (viewer, screen) pair and JSON has no
        way to express that as an object key."""
        return self.serialize_assignments()

    def serialize_assignments(self) -> list[dict]:
        # window_id is carried alongside screen_name, even though the
        # assignment is no longer keyed on it, because it is what the web UI
        # addresses a window by. It is None for an output with no window right
        # now - unplugged, or switched off by screen power control - which is
        # precisely the state the assignment is being kept for.
        return [
            {'webview_id': vid, 'screen_name': name,
             'window_id': self._window_of(vid, name), 'playlist_id': pid}
            for (vid, name), pid in self._assignments.items()
        ]
