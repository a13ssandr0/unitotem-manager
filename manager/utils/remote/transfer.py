"""
Moving playlists and uploaded files between two nodes over the link: when a client is adopted (its content goes to the
server) and when it is unpaired (what its screens were showing goes back to it).

The rules of WHAT moves (`plan_leave`) are plain functions on plain data. HOW it moves is a small protocol on the control
frames of the link (utils.remote.link), built to be safe rather than fast:

  sender                                   receiver
  transfer-begin {id, manifest}    ->
                                   <-      transfer-want {files not already here}
  file-begin {name,size,sha256}    ->      (bytes frames of at most CHUNK, written to a staging area)
  file-end                         ->      (size and sha256 checked; a bad file is thrown away)
  transfer-commit                  ->      (all of it is applied, or none of it)
                                   <-      transfer-done | transfer-error {reason}

* nothing is removed from the sender until the receiver said `transfer-done`: a failure at any point leaves the sender
  exactly as it was, and the receiver with at most some staged files;
* files are streamed in chunks (an upload can be many GiB) and verified by SHA-256 before they are used;
* the id of a transfer is derived from its content, so a retry after a lost answer is recognised and does not apply twice;
* a file the receiver already has (same name and same hash) is not sent again, which is also what makes a retry resumable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

import aiofiles
from loguru import logger

from utils.remote.link import control_frame, is_control, parse_control

CHUNK = 512 * 1024
MAX_FILES = 5000
MAX_PLAYLISTS = 500
MAX_FILE_SIZE = 1 << 40
STEP_TIMEOUT = 120               # the longest silence tolerated from the other side
_SHA256 = re.compile(r'[0-9a-f]{64}')


class TransferError(Exception):
    """The transfer failed; the message is for the log and the page"""


# ── what moves ────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    playlists: list[str]                    # sent to the other node
    files: set[str]                         # sent to the other node
    assignments: list[tuple[str, str]]     # (screen name, playlist id) of the windows of the node that leaves
    remove_playlists: set[str]              # removed here after the other node has everything
    remove_files: set[str]                  # likewise


def plan_leave(playlist_files: dict[str, set[str]], assignments: list[tuple[str, str, str]], leaving: str,
               default: Optional[str] = None) -> Plan:
    """What goes with a node that is unpaired, and what stays behind.

    `playlist_files`: every playlist of this node and the uploaded files it uses. `assignments`: (viewer id, screen name,
    playlist id) for every window of every viewer, the leaving node's among them. `default` is the playlist this node's
    own calls act on and which it can never be without.

    * what the leaving node's windows show is copied to it;
    * a playlist that only the leaving node shows is removed here afterwards: it has no other user. One that another
      viewer also shows, or that is the default one, stays untouched;
    * a file is removed here only if every playlist using it is removed too: a file used by anything that stays, stays.
    Playlists nobody shows are not the leaving node's business and are never touched.
    """
    mine = [(screen, pid) for viewer, screen, pid in assignments if viewer == leaving and pid in playlist_files]
    sent = list(dict.fromkeys(pid for _screen, pid in mine))
    shared = {pid for viewer, _screen, pid in assignments if viewer != leaving}
    if default is not None:
        shared.add(default)
    remove_playlists = {pid for pid in sent if pid not in shared}
    files = set().union(*(playlist_files[pid] for pid in sent)) if sent else set()
    remove_files = {f for f in files
                    if all(pid in remove_playlists for pid, used in playlist_files.items() if f in used)}
    return Plan(sent, files, mine, remove_playlists, remove_files)


# ── manifest ──────────────────────────────────────────────────────────────────

def safe_name(name) -> bool:
    """A file directly in the uploads folder: no folders, no way up, nothing hidden"""
    return (isinstance(name, str) and 0 < len(name.encode()) <= 255 and '/' not in name and '\\' not in name
            and '\x00' not in name and not name.startswith('.'))


def make_id(playlists: Iterable[dict], files: Iterable[dict], scope: str) -> str:
    """The same content always has the same id: a retry is recognised as such"""
    digest = hashlib.sha256(json.dumps([scope, sorted(p['playlist_id'] for p in playlists),
                                        sorted((f['name'], f['sha256']) for f in files)]).encode())
    return digest.hexdigest()[:32]


def validate_manifest(manifest) -> dict:
    """What the other node announced, checked like any input: bounded, and every file name a plain one."""
    try:
        if not isinstance(manifest, dict):
            raise ValueError('not an object')
        tid = manifest.get('id')
        if not isinstance(tid, str) or not re.fullmatch(r'[0-9a-f]{32}', tid):
            raise ValueError('bad id')
        playlists, files, assignments = manifest.get('playlists'), manifest.get('files'), manifest.get('assignments')
        if not (isinstance(playlists, list) and isinstance(files, list) and isinstance(assignments, list)):
            raise ValueError('bad lists')
        if len(playlists) > MAX_PLAYLISTS or len(files) > MAX_FILES or len(assignments) > 1000:
            raise ValueError('too many items')
        for p in playlists:
            if not isinstance(p, dict) or not isinstance(p.get('playlist_id'), str) or not isinstance(p.get('assets'), list):
                raise ValueError('bad playlist')
        names = set()
        for f in files:
            if (not isinstance(f, dict) or not safe_name(f.get('name')) or f['name'] in names
                    or not isinstance(f.get('size'), int) or not 0 <= f['size'] <= MAX_FILE_SIZE
                    or not isinstance(f.get('sha256'), str) or not _SHA256.fullmatch(f['sha256'])):
                raise ValueError('bad file entry')
            names.add(f['name'])
        for a in assignments:
            if not (isinstance(a, (list, tuple)) and len(a) == 2 and all(isinstance(x, str) for x in a)):
                raise ValueError('bad assignment')
    except ValueError as e:
        raise TransferError(f'Invalid transfer: {e}') from None
    return manifest


# ── the channel ───────────────────────────────────────────────────────────────

RECEIVER_KINDS = {'transfer-begin', 'file-begin', 'file-end', 'transfer-commit', 'transfer-abort'}


class Channel:
    """One end of the control traffic of a link: sends frames and sorts what arrives into the part that is receiving a
    transfer (`rx`) and the part that is sending one (`replies`). Bounded: a sender faster than the disk is slowed down,
    not buffered."""

    def __init__(self, send: Callable[[object], Awaitable]):
        self._send = send
        self.rx: asyncio.Queue = asyncio.Queue(maxsize=8)
        self.replies: asyncio.Queue = asyncio.Queue(maxsize=8)
        self.receiving: Optional[asyncio.Task] = None
        self.on_other: Optional[Callable[[dict], Awaitable]] = None     # control messages that are not about transfers
        self.on_begin: Optional[Callable[['Channel', dict], Awaitable]] = None

    async def send_json(self, **fields):
        await self._send(control_frame(**fields))

    async def send_bytes(self, data: bytes):
        await self._send(data)

    async def dispatch(self, raw):
        """Called by the link for every control frame received"""
        if isinstance(raw, bytes):
            await self.rx.put(raw)
            return
        msg = parse_control(raw)
        kind = msg.get('link')
        if kind == 'transfer-begin':
            if self.receiving is not None and not self.receiving.done():
                await self.send_json(link='transfer-error', id=msg.get('id'), reason='Another transfer is in progress')
                return
            while not self.rx.empty():
                self.rx.get_nowait()
            self.receiving = asyncio.get_running_loop().create_task(self.on_begin(self, msg), name='transfer-receive')
        elif kind in RECEIVER_KINDS:
            await self.rx.put(msg)
        elif kind in ('transfer-want', 'transfer-done', 'transfer-error'):
            await self.replies.put(msg)
        elif self.on_other is not None:
            await self.on_other(msg)

    def close(self):
        if self.receiving is not None:
            self.receiving.cancel()

    async def reply(self, *kinds: str, tid: str) -> dict:
        while True:
            try:
                msg = await asyncio.wait_for(self.replies.get(), STEP_TIMEOUT)
            except asyncio.TimeoutError:
                raise TransferError('The other node stopped answering') from None
            if msg.get('id') != tid:
                continue                    # an answer to something older
            if msg['link'] == 'transfer-error':
                raise TransferError(str(msg.get('reason') or 'The other node refused the transfer')[:300])
            if msg['link'] in kinds:
                return msg


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


# ── sending ───────────────────────────────────────────────────────────────────

async def send_bundle(channel: Channel, manifest: dict, folder: Path,
                      progress: Optional[Callable[[int, int], None]] = None):
    """Send everything the manifest announces. Returns when the receiver has applied it (transfer-done); raises
    TransferError if anything went wrong, in which case the caller must not remove anything."""
    tid = manifest['id']
    await channel.send_json(link='transfer-begin', id=tid, manifest=manifest)
    want = (await channel.reply('transfer-want', 'transfer-done', tid=tid))
    if want['link'] == 'transfer-done':
        return                                       # applied before: this was a retry
    wanted = set(want.get('files') or [])
    by_name = {f['name']: f for f in manifest['files']}
    if not wanted <= set(by_name):
        raise TransferError('The other node asked for files that are not in the transfer')
    total = sum(by_name[n]['size'] for n in wanted)
    sent = 0
    for name in sorted(wanted):
        entry = by_name[name]
        await channel.send_json(link='file-begin', id=tid, name=name, size=entry['size'], sha256=entry['sha256'])
        async with aiofiles.open(folder / name, 'rb') as f:
            written = 0
            while chunk := await f.read(CHUNK):
                written += len(chunk)
                await channel.send_bytes(chunk)
                sent += len(chunk)
                if progress:
                    progress(sent, total)
        if written != entry['size']:
            await channel.send_json(link='transfer-abort', id=tid)
            raise TransferError(f'{name} changed while it was being sent')
        await channel.send_json(link='file-end', id=tid, name=name)
    await channel.send_json(link='transfer-commit', id=tid)
    await channel.reply('transfer-done', tid=tid)


# ── receiving ─────────────────────────────────────────────────────────────────

@dataclass
class Receiver:
    """Where a transfer is staged and what it needs from the node that applies it"""
    stage_root: Path                                   # one folder per transfer id below this
    uploads: Path                                      # to find what is already here
    apply: Callable[[dict, Path], Awaitable]           # (manifest, staged files folder) -> applies it all or raises
    committed: set = field(default_factory=set)        # ids already applied (see make_id)
    free_space: Callable[[], int] = lambda: shutil.disk_usage('/var/lib').free


async def receive_bundle(channel: Channel, first: dict, receiver: Receiver):
    """The receiving half, started when `transfer-begin` arrives. Always ends by telling the sender how it went."""
    tid = first.get('id')
    stage: Optional[Path] = None
    try:
        manifest = validate_manifest(first.get('manifest'))
        tid = manifest['id']
        if tid in receiver.committed:
            await channel.send_json(link='transfer-done', id=tid)
            return
        stage = receiver.stage_root / tid
        (stage / 'files').mkdir(parents=True, exist_ok=True)
        files = stage / 'files'

        wanted = []
        for entry in manifest['files']:
            staged, existing = files / entry['name'], receiver.uploads / entry['name']
            if staged.is_file() and staged.stat().st_size == entry['size'] and await asyncio.to_thread(
                    sha256_file, staged) == entry['sha256']:
                continue                             # staged by an attempt that was interrupted: resume
            if existing.is_file() and existing.stat().st_size == entry['size'] and await asyncio.to_thread(
                    sha256_file, existing) == entry['sha256']:
                continue                             # the same file is already here
            wanted.append(entry)
        wanted.sort(key=lambda e: e['name'])           # the order the sender sends them in
        need = sum(e['size'] for e in wanted)
        if need + (1 << 28) > receiver.free_space():
            raise TransferError('Not enough free space on the receiving node')
        await channel.send_json(link='transfer-want', id=tid, files=[e['name'] for e in wanted])

        for entry in wanted:
            begin = await _next(channel, 'file-begin')
            if (begin.get('name'), begin.get('size'), begin.get('sha256')) != (entry['name'], entry['size'], entry['sha256']):
                raise TransferError('The sender did not send what was asked for')
            part = files / (entry['name'] + '.part')
            h, got = hashlib.sha256(), 0
            async with aiofiles.open(part, 'wb') as out:
                while True:
                    item = await _next(channel, None)
                    if isinstance(item, bytes):
                        got += len(item)
                        if got > entry['size']:
                            raise TransferError(f"{entry['name']} is bigger than announced")
                        h.update(item)
                        await out.write(item)
                    elif item.get('link') == 'file-end' and item.get('name') == entry['name']:
                        break
                    else:
                        raise TransferError('Unexpected message while receiving a file')
            if got != entry['size'] or h.hexdigest() != entry['sha256']:
                part.unlink(missing_ok=True)
                raise TransferError(f"{entry['name']} arrived damaged")
            part.replace(files / entry['name'])

        await _next(channel, 'transfer-commit')
        await receiver.apply(manifest, files)
        receiver.committed.add(tid)
        await channel.send_json(link='transfer-done', id=tid)
        shutil.rmtree(stage, ignore_errors=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        reason = str(e) if isinstance(e, TransferError) else 'The receiving node failed to apply the transfer'
        if not isinstance(e, TransferError):
            logger.exception('Applying a transfer failed')
        else:
            logger.warning('Transfer {} failed: {}', tid, e)
        try:
            await channel.send_json(link='transfer-error', id=tid, reason=reason)
        except Exception:
            pass
        # the staged files are kept: they are what lets a retry skip them (stale ones are cleaned up at start)


async def _next(channel: Channel, kind: Optional[str]):
    try:
        item = await asyncio.wait_for(channel.rx.get(), STEP_TIMEOUT)
    except asyncio.TimeoutError:
        raise TransferError('The sender stopped sending') from None
    if isinstance(item, dict) and item.get('link') == 'transfer-abort':
        raise TransferError('The sender cancelled the transfer')
    if kind is not None:
        if not (isinstance(item, dict) and item.get('link') == kind):
            raise TransferError(f'Expected {kind}')
    return item


def clean_stale(stage_root: Path, max_age: float = 86400):
    """Staging folders of transfers nobody finished"""
    if not stage_root.is_dir():
        return
    for d in stage_root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < time.time() - max_age:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
