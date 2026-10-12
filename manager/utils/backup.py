"""
What can be backed up, restored and reset, as a list of selectable parts.

The core contributes the parts that belong to an orchestrator (playlists, users, the pairing with other nodes, the
uploaded files); a plugin adds its own (see utils.plugins.BackupPart), so the page offers exactly what this install
can restore. The archive is a zip with `config.json` ({"version", "parts": {key: data}}) and, for the parts made of
files, the files themselves.
"""
from __future__ import annotations

import inspect
import json
from typing import Optional

import utils.plugins as plugins
from utils.models.assets import AssetsManager
from utils.models.playlists import playlists_manager
from utils.models.remote import RemoteManager
from utils.models.user import UserPerms, user_manager
from utils.plugins import BackupPart
from utils.storage.uploadmanager import upload_manager


# ── playlists ────────────────────────────────────────────────────────────────

def _export_playlists():
    return {'playlists': {pid: json.loads(am.model_dump_json()) for pid, am in playlists_manager.playlists.items()},
            'default': playlists_manager._default_id}


def _restore_playlists(data, _version):
    playlists_manager._playlists.clear()
    for pid, am_data in data['playlists'].items():
        am = AssetsManager.model_validate(am_data)
        am._filepath = playlists_manager._playlist_path(am.playlist_id)
        am.save()
        playlists_manager._playlists[pid] = am
    playlists_manager._default_id = data.get('default')
    playlists_manager._save_index()


def _reset_playlists():
    for am in playlists_manager.playlists.values():
        am.assets.clear()
        am.save()


# ── users ────────────────────────────────────────────────────────────────────

def _restore_users(data, _version):
    user_manager.__init__(**data)
    user_manager.save()


def _reset_users():
    user_manager.root.clear()
    user_manager.add_user('admin', 'admin', {UserPerms.admin})


# ── pairing with other nodes (the keys and certificates exchanged at pairing time) ──

def _export_remote():
    return RemoteManager.get_instance().model_dump()


def _restore_remote(data, _version):
    RemoteManager(**data).save()


def _reset_remote():
    RemoteManager().save()


# ── uploaded files ───────────────────────────────────────────────────────────

def _export_files(zip_file):
    for file in upload_manager.files:
        zip_file.write(file.absolute(), 'uploaded/' + file.name)


async def _restore_files(zip_file):
    for name in zip_file.namelist():
        if name.startswith('uploaded/') and not name.endswith('/'):
            with zip_file.open(name) as infile:
                await upload_manager.save(infile, name)


def _reset_files():
    for file in list(upload_manager.files):
        upload_manager.remove(file)


CORE_PARTS = [
    BackupPart('playlists', 'Playlists', _export_playlists, _restore_playlists, _reset_playlists),
    BackupPart('users', 'Users', lambda: json.loads(user_manager.model_dump_json()), _restore_users, _reset_users),
    BackupPart('remote', 'Pairing and certificates', _export_remote, _restore_remote, _reset_remote),
    BackupPart('uploads', 'Uploaded files', None, None, _reset_files, _export_files, _restore_files,
               optional_in_backup=True),
]


def parts() -> dict[str, BackupPart]:
    found = {part.key: part for part in CORE_PARTS}
    for _plugin, plugin_parts in plugins.each('backup'):
        for part in plugin_parts:
            found[part.key] = part
    return found


def describe() -> list[dict]:
    return [{'key': p.key, 'label': p.label, 'files': p.export_files is not None,
             'default_in_backup': not p.optional_in_backup} for p in parts().values()]


async def maybe_await(result):
    if inspect.isawaitable(result):
        await result


def from_legacy(config: dict) -> dict:
    """Archives written before the parts existed: {"CONFIG": {playlists, playlists_default, users}, "hostname",
    "def_audio_dev", "netplan", "cron"} become the equivalent parts."""
    out = dict(config.get('parts', {}))
    cfg = config.get('CONFIG')
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    if cfg:
        if 'playlists' in cfg:
            out.setdefault('playlists', {'playlists': cfg['playlists'], 'default': cfg.get('playlists_default')})
        if 'users' in cfg:
            out.setdefault('users', cfg['users'])
    for old, new in (('hostname', 'hostname'), ('def_audio_dev', 'audio'), ('netplan', 'netplan')):
        if old in config:
            out.setdefault(new, config[old])
    return out
