"""
The files a client shows. After adoption the uploads live on the server, but the windows of a client ask THEIR OWN manager for `/uploaded/<name>`: the
server therefore sends each client the files of the playlists assigned to its windows (the panes' too), over the same mutually authenticated link and with the
same staging and hash checks as any other transfer (utils.remote.transfer), and the client keeps them in a folder of its own, served beside its uploads.

* the client says what it has (`cache-have`: name -> SHA-256) when it connects and after each transfer; the server sends only what differs;
* what the client has and the server no longer wants is removed (`keep` in the manifest is everything wanted, so one list says both);
* the cache is state, not configuration: it is not part of a backup, and a node that leaves its server empties it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from utils.remote import transfer
from utils.storage.uploadmanager import upload_manager

FOLDER = 'remote-files'
MAX_NAMES = transfer.MAX_FILES
_hashes: dict[tuple[str, int, int], str] = {}        # (path, size, mtime) -> sha256: a big file is hashed once


def folder() -> Path:
    """Beside the uploads (on the data partition: it must survive a reboot, a client plays on without its server)."""
    return upload_manager.folder.parent / FOLDER


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _hashes:
        _hashes[key] = transfer.sha256_file(path)
    return _hashes[key]


def inventory(directory: Optional[Path] = None) -> dict[str, str]:
    """name -> SHA-256 of the files in a folder (the cache of this node by default). Blocking: run it in a thread."""
    directory = directory or folder()
    found: dict[str, str] = {}
    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and transfer.safe_name(entry.name) and not entry.name.endswith('.part'):
                found[entry.name] = _sha256(entry)
    except OSError:
        pass
    return found


def clean_have(value) -> dict[str, str]:
    """What a client says it has, as far as it can be believed: bounded, plain names and real SHA-256 digests."""
    if not isinstance(value, dict) or len(value) > MAX_NAMES:
        return {}
    return {name: digest for name, digest in value.items()
            if transfer.safe_name(name) and isinstance(digest, str) and transfer._SHA256.fullmatch(digest)}


def wanted_names(viewer_id: str) -> set[str]:
    """The uploaded files the windows of a viewer need: those of the playlists assigned to them, the main pane and the other panes alike."""
    from utils.remote import bundle
    files = bundle.playlist_files()
    playlists = {pid for vid, _screen, pid in bundle.assignments() if vid == viewer_id}
    playlists |= set(bundle.pane_playlists(viewer_id))
    return set().union(*(files.get(pid, set()) for pid in playlists)) if playlists else set()


async def build_manifest(have: dict[str, str], wanted: Iterable[str], source: Optional[Path] = None) -> Optional[dict]:
    """The transfer that brings a client up to date, or None when it already is: the files it lacks or has different, and `keep`, everything it
    must have after (the rest of its cache goes)."""
    source = source or upload_manager.folder
    present = [name for name in sorted(set(wanted)) if (source / name).is_file()]
    files = []
    for name in present:
        digest = await asyncio.to_thread(_sha256, source / name)
        if have.get(name) != digest:
            files.append({'name': name, 'size': (source / name).stat().st_size, 'sha256': digest})
    stale = set(have) - set(present)
    if not files and not stale:
        return None
    return {'id': os.urandom(16).hex(), 'kind': 'cache', 'playlists': [], 'files': files, 'assignments': [], 'windows': {}, 'keep': present}


def apply_cache(manifest: dict, staged: Path, target: Optional[Path] = None):
    """The client side of such a transfer: the new files go into the cache (replacing the ones with the same name), the ones nobody wants go out."""
    target = target or folder()
    target.mkdir(parents=True, exist_ok=True)
    keep = {name for name in manifest.get('keep', []) if transfer.safe_name(name)}
    for entry in manifest['files']:
        if (staged / entry['name']).is_file():          # a file the receiver already had identical was not sent again
            shutil.move(str(staged / entry['name']), str(target / entry['name']))
    for entry in list(target.iterdir()):
        if entry.is_file() and (entry.name not in keep or entry.name.endswith('.part')):
            entry.unlink(missing_ok=True)
    logger.info('Remote file cache: {} received, {} kept', len(manifest['files']), len(keep))


def clear(target: Optional[Path] = None):
    """This node does not belong to a server any more."""
    shutil.rmtree(target or folder(), ignore_errors=True)
