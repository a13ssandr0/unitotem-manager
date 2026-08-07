from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger

from utils.environment import environ
from utils.models.assets import AssetsManager
from utils.models.command_line import cmdargs


class PlaylistsManager:
    """Manages multiple AssetsManager playlist instances"""

    _instance: Optional[PlaylistsManager] = None

    def __init__(self):
        self._playlists: dict[str, AssetsManager] = {}
        self._default_id: Optional[str] = None

    @classmethod
    def get_instance(cls) -> PlaylistsManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def playlists(self) -> dict[str, AssetsManager]:
        return self._playlists

    @property
    def default(self) -> AssetsManager:
        if self._default_id and self._default_id in self._playlists:
            return self._playlists[self._default_id]
        if self._playlists:
            return next(iter(self._playlists.values()))
        raise RuntimeError("No playlists available")

    def get(self, playlist_id: Optional[str] = None) -> AssetsManager:
        if playlist_id is None:
            return self.default
        if playlist_id not in self._playlists:
            raise KeyError(f"Playlist {playlist_id!r} not found")
        return self._playlists[playlist_id]

    def create(self, name: str = 'New Playlist') -> AssetsManager:
        am = AssetsManager(name=name)
        self._playlists[am.playlist_id] = am
        am._filepath = self._playlist_path(am.playlist_id)
        am.save()
        self._save_index()
        return am

    def delete(self, playlist_id: str):
        if playlist_id == self._default_id:
            raise ValueError("Cannot delete the default playlist")
        if len(self._playlists) <= 1:
            raise ValueError("Cannot delete the last playlist")
        am = self._playlists.pop(playlist_id, None)
        if am and am._filepath and am._filepath.exists():
            am._filepath.unlink()
        self._save_index()

    def _playlist_path(self, playlist_id: str) -> Path:
        return Path(cmdargs.assets_file).parent / f'playlist_{playlist_id}.json'

    def _index_path(self) -> Path:
        return Path(cmdargs.assets_file).parent / 'playlists.json'

    def _save_index(self):
        # A configuration has been written, so this node is no longer on its
        # first boot: the welcome screen must give way to the "no assets"
        # placeholder when a playlist runs empty. Mirrors the original
        # behaviour, where saving or loading the configuration cleared the flag
        # and only a reset set it again.
        environ._unitotem_first_boot = False
        index = {
            'default': self._default_id,
            'playlists': {pid: str(am._filepath) for pid, am in self._playlists.items()}
        }
        with open(self._index_path(), 'w') as f:
            json.dump(index, f, indent=4)

    def load(self):
        index_path = self._index_path()
        if index_path.exists():
            # Configuration found on disk: the node has been set up before.
            environ._unitotem_first_boot = False
            with open(index_path) as f:
                index = json.load(f)
            self._default_id = index.get('default')
            for pid, filepath in index.get('playlists', {}).items():
                am = AssetsManager(playlist_id=pid)
                try:
                    am.load(filepath)
                    self._playlists[pid] = am
                except FileNotFoundError:
                    logger.warning('Playlist file not found: {}', filepath)
            if self._playlists and self._default_id not in self._playlists:
                self._default_id = next(iter(self._playlists))
            if not self._playlists:
                # every referenced playlist file is missing: self-heal with an
                # empty default so the manager always has at least one playlist
                logger.warning('No playlist could be loaded, recreating an empty default')
                am = self.create('Default')
                self._default_id = am.playlist_id
                self._save_index()
        else:
            # Legacy: single assets.json → become the default playlist
            am = AssetsManager()
            configured = True
            try:
                am.load(cmdargs.assets_file)
            except FileNotFoundError:
                logger.warning('No assets file found, starting with empty default playlist')
                configured = False
            am._filepath = self._playlist_path(am.playlist_id)
            self._playlists[am.playlist_id] = am
            self._default_id = am.playlist_id
            am.save()
            self._save_index()
            # _save_index() clears the first-boot flag, because writing a
            # configuration normally means somebody configured the node. The
            # write above is our own bootstrap, not an operator doing anything,
            # so the flag has to be restored to the truth: a node with neither
            # playlists.json nor a legacy assets.json has never been set up and
            # must show the welcome screen with the hotspot credentials, which
            # are the only way into a node that has no network yet.
            environ._unitotem_first_boot = not configured

    def serialize(self) -> list[dict]:
        return [
            {
                'playlist_id': pid,
                'name': am.name,
                'asset_count': len(am.assets),
                'enabled_count': am.count_enabled(),
                'is_default': pid == self._default_id,
            }
            for pid, am in self._playlists.items()
        ]


playlists_manager = PlaylistsManager.get_instance()
