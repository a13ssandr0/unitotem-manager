from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger

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

    def rename(self, playlist_id: str, name: str):
        name = name.strip()
        if not name:
            raise ValueError('A playlist needs a name')
        am = self.get(playlist_id)
        am.name = name
        am.save()

    def set_default(self, playlist_id: str):
        """The default playlist is the one calls without a playlist_id act on, and the one that cannot be deleted."""
        self.get(playlist_id)  # raises KeyError for an unknown one
        self._default_id = playlist_id
        self._save_index()

    def transfer_assets(self, uuids: list[str], source_id: str, target_id: str, move: bool = False) -> list[str]:
        """Copy (or move) assets from one playlist to another, keeping their order. A copy gets a new uuid: the
        same asset in two playlists would share timers and a "current" marker. Returns the new uuids."""
        if source_id == target_id:
            raise ValueError('Source and target are the same playlist')
        source, target = self.get(source_id), self.get(target_id)
        created = []
        wanted = set(uuids)
        for asset in [a for a in source.assets if a.uuid in wanted]:
            data = asset.model_dump(mode='json', exclude={'uuid'})
            target.append(data)
            created.append(target.assets[-1].uuid)
        if move:
            for uuid in wanted:
                del source[uuid]
            source.revalidate_current()
        target.revalidate_current()
        target.save()
        return created

    def referenced_files(self) -> dict[str, list[str]]:
        """Which playlists use each uploaded file: {filename: [playlist_id, ...]}."""
        used: dict[str, list[str]] = {}
        for pid, am in self._playlists.items():
            for asset in am.assets:
                if asset.url.startswith('file:'):
                    used.setdefault(asset.url.removeprefix('file:'), []).append(pid)
        return used

    def _playlist_path(self, playlist_id: str) -> Path:
        return Path(cmdargs.assets_file).parent / f'playlist_{playlist_id}.json'

    def _index_path(self) -> Path:
        return Path(cmdargs.assets_file).parent / 'playlists.json'

    def _save_index(self):
        index = {
            'default': self._default_id,
            'playlists': {pid: str(am._filepath) for pid, am in self._playlists.items()}
        }
        path = self._index_path()
        tmp = path.with_name(path.name + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(index, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)

    def load(self):
        index_path = self._index_path()
        if index_path.exists():
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
            try:
                am.load(cmdargs.assets_file)
            except FileNotFoundError:
                logger.warning('No assets file found, starting with empty default playlist')
            am._filepath = self._playlist_path(am.playlist_id)
            self._playlists[am.playlist_id] = am
            self._default_id = am.playlist_id
            am.save()
            self._save_index()

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
