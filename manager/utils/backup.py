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

from loguru import logger

import utils.plugins as plugins
from utils import osusers, sessions
from utils.models.assets import AssetsManager
from utils.models.playlists import playlists_manager
from utils.models.remote import RemoteManager
from utils.models.user import user_manager
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

def _export_users():
    """Each user with its capabilities and the password as the system keeps it (the hash of /etc/shadow, never the
    password): the archive is as sensitive as the node's private keys."""
    return {name: {'permissions': sorted(data.permissions), 'shadow': osusers.password_hash(name)}
            for name, data in user_manager.items()}


def _restore_users(data, _version):
    """Recreates the accounts (system user, group, password) and their capabilities. An account of an archive from
    before the users were the system's has no recoverable password and is skipped: if that leaves no administrator
    the node asks for the first-access setup."""
    restored = {}
    for name, entry in data.items():
        shadow = entry.get('shadow')
        if not shadow:
            logger.warning('The backup has no password for the user {}: not restored', name)
            continue
        try:
            osusers.restore_user(name, shadow)
        except osusers.OSUserError as e:
            logger.error('Cannot restore the user {}: {}', name, e)
            continue
        restored[name] = {'permissions': entry.get('permissions', [])}
    user_manager.__init__(**restored)
    user_manager.save()
    sessions.close_soon()           # every password may be another one now: everybody logs in again


def _reset_users():
    """No users at all: the accounts UniTotem made are deleted, and the node waits for the first-access setup."""
    for name in list(user_manager.users()):
        osusers.remove_user(name)
    user_manager.root.clear()
    user_manager.save()
    sessions.close_soon()


# ── security settings ────────────────────────────────────────────────────────

def _export_security():
    from utils.models.security import security_settings
    return security_settings.model_dump()


def _restore_security(data, _version):
    from utils.models.security import SecuritySettings, security_settings
    security_settings.enforce_password_policy = SecuritySettings(**data).enforce_password_policy
    security_settings.save()


def _reset_security():
    from utils.models.security import security_settings
    security_settings.enforce_password_policy = False
    security_settings.save()


# ── pairing with other nodes (the keys and certificates exchanged at pairing time) ──

def _export_remote():
    """The identity of this node (certificate and private key), the certificates pinned at pairing time and the role.
    The archive is as secret as the keys inside it."""
    RemoteManager.get_instance().ensure_identity()      # a backup taken before the first pairing still carries the identity
    return RemoteManager.get_instance().model_dump(mode='json')


def _restore_remote(data, _version):
    RemoteManager.restore(data)
    _remote_changed()


def _reset_remote():
    RemoteManager.reset()
    _remote_changed()


def _remote_changed():
    """The listener / the connection to the server were built from the old state: rebuild them from the new one"""
    import asyncio
    from utils.remote.service import service
    try:
        asyncio.get_running_loop().create_task(service.apply_role())
    except RuntimeError:
        pass        # no loop: the next start reads the new state anyway


# ── RSS ticker (the sources and the bar of each window; what the feeds said is cache, not backed up) ──

def _export_ticker():
    import utils.ticker as ticker
    return {'sources': [s.model_dump() for s in ticker.store().sources], 'windows': ticker.windows().export()}


def _restore_ticker(data, _version):
    import utils.ticker as ticker
    ticker.store().set_sources(data.get('sources', []))
    ticker.windows().replace(data.get('windows', {}))
    _ticker_changed()


def _reset_ticker():
    import utils.ticker as ticker
    ticker.store().set_sources([])
    ticker.windows().replace({})
    _ticker_changed()


def _ticker_changed():
    """The scheduler and the windows were working from the old state: tell them (nothing to do before the loop runs)."""
    import asyncio
    import utils.ticker as ticker
    from utils.ticker import push
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def tell():
        from webview.controller import WebviewManager
        ticker.service().poke()
        await ticker.service().items_changed()
        vm = WebviewManager.get_instance()
        await push.refresh_all(vm.send_webview_command, vm.get_webviews())
    loop.create_task(tell())


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
    BackupPart('users', 'Users', _export_users, _restore_users, _reset_users),
    BackupPart('security', 'Security settings', _export_security, _restore_security, _reset_security),
    BackupPart('remote', 'Pairing and certificates', _export_remote, _restore_remote, _reset_remote),
    BackupPart('ticker', 'News ticker (RSS sources and bars)', _export_ticker, _restore_ticker, _reset_ticker),
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
