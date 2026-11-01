"""
The live side of the transfer (utils.remote.transfer): gathering what leaves this node, applying what arrives, and
removing what has been handed over. The rules are in transfer.plan_leave; this only reads and changes the real
playlists, uploads and assignments.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from utils.models.assets import AssetsManager
from utils.models.playlists import playlists_manager
from utils.remote import transfer
from utils.remote.transfer import Plan, TransferError
from utils.storage.uploadmanager import upload_manager

LOCAL_VIEWER = 'local-webview'      # how the viewer of this very node is known to its own manager
# the per-window settings of viewer_settings.json that travel with a node's windows (each is keyed "<viewer id>|<screen name>")
# an output's name as a connector gives it (`HDMI-A-1`, `DP-2`): what a screen may be called in a transfer
_SCREEN_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,63}')
WINDOW_FIELDS = ('audio_outputs', 'keyboards', 'orientations', 'flips', 'screensavers', 'refreshes', 'languages')


def _vm():
    from webview.controller import WebviewManager
    try:
        return WebviewManager.get_instance()
    except RuntimeError:
        return None


def playlist_files() -> dict[str, set[str]]:
    """Every playlist and the uploaded files it uses"""
    return {pid: {a.url.removeprefix('file:') for a in am.assets if a.url.startswith('file:')}
            for pid, am in playlists_manager.playlists.items()}


def assignments() -> list[tuple[str, str, str]]:
    """(viewer id, screen name, playlist id) for every window of every viewer"""
    vm = _vm()
    if vm is None:
        return []
    return [(a['webview_id'], a['screen_name'], a['playlist_id']) for a in vm.get_assignments() if a['screen_name']]


def collect_windows(viewer_id: str) -> dict[str, dict]:
    """What the windows of a viewer are like, per screen name: the settings of viewer_settings.json (audio output, keyboard, turn, mirror,
    screensaver, refresh, language) and the layout (area, panes, ticker). They go with the node that leaves, with its playlists."""
    from utils.models.layout import window_layouts
    from utils.models.viewer import viewer_settings
    prefix = f'{viewer_id}|'
    windows: dict[str, dict] = {}
    for field_name in WINDOW_FIELDS:
        for key, value in getattr(viewer_settings, field_name).items():
            if key.startswith(prefix):
                windows.setdefault(key[len(prefix):], {})[field_name] = value.model_dump() if hasattr(value, 'model_dump') else value
    for key, layout in window_layouts.windows.items():
        if key.startswith(prefix):
            windows.setdefault(key[len(prefix):], {})['layout'] = json.loads(layout.model_dump_json())
    return windows


def pane_playlists(viewer_id: str) -> list[str]:
    """The playlists shown by the panes (other than the main ones) of the windows of a viewer."""
    from utils.models.layout import window_layouts
    prefix = f'{viewer_id}|'
    return [pid for key, layout in window_layouts.windows.items() if key.startswith(prefix) for pid in layout.panes.values()]


def plan_for(viewer_id: str) -> Plan:
    """What goes with the node whose windows are known here as `viewer_id`"""
    default = playlists_manager._default_id
    used = assignments()
    vm = _vm()
    if vm is not None:
        # the panes of the other windows use playlists too: they must not be taken for unused and removed
        used += [(vid, screen, pid) for vid, screen, _pane, pid in vm.pane_assignments() if vid != viewer_id]
    plan = transfer.plan_leave(playlist_files(), used, viewer_id, default, panes=pane_playlists(viewer_id))
    plan.windows = collect_windows(viewer_id)
    return plan


def plan_everything() -> Plan:
    """A node that joins a server takes nothing with it that it still needs: everything it has moves, and is removed
    here afterwards. (The same shape as a plan so that one code path sends it.)"""
    files = playlist_files()
    mine = [(screen, pid) for viewer, screen, pid in assignments() if viewer == LOCAL_VIEWER and pid in files]
    allfiles = set().union(*files.values()) if files else set()
    return Plan(list(files), allfiles, mine, set(files), allfiles, collect_windows(LOCAL_VIEWER))


async def build_manifest(plan: Plan, scope: str) -> dict:
    """The manifest of a plan: the playlists as they are now, and size and SHA-256 of each uploaded file."""
    playlists = [json.loads(playlists_manager.playlists[pid].model_dump_json()) for pid in plan.playlists]
    files = []
    for name in sorted(plan.files):
        path = upload_manager.folder / name
        if not path.is_file():
            logger.warning('Playlist refers to {} which is not in the uploads: not transferred', name)
            continue
        files.append({'name': name, 'size': path.stat().st_size, 'sha256': await asyncio.to_thread(transfer.sha256_file, path)})
    return {'id': transfer.make_id(playlists, files, scope), 'playlists': playlists, 'files': files,
            'assignments': [list(a) for a in plan.assignments], 'windows': plan.windows}


def _bind(am: AssetsManager):
    """New playlists announce their changes to the web UI, like the ones created from the page"""
    try:
        from api.ws.endpoints import api
        api.generators['Scheduler/create_playlist'].__original_func__.__self__._bind_playlist_callbacks(am)
    except Exception:
        logger.warning('Could not connect the playlist {} to the web UI updates', am.playlist_id)


def apply_windows(windows: dict, viewer_id: str, id_map: dict[str, str]) -> Callable[[], None]:
    """Make the windows of the node that arrived part of this one, under the name `viewer_id` gives them here: the settings of every screen
    and its layout, whose panes' playlists are the ones that were just created (`id_map`: their old ids to the new ones). What is not valid is
    left out, never the transfer. Returns the function that puts everything back as it was."""
    from pydantic import ValidationError
    from utils.models.layout import WindowLayout, window_layouts
    from utils.models.viewer import (KeyboardSettings, RefreshSettings, ScreensaverSettings, valid_language, viewer_settings)
    models = {'keyboards': KeyboardSettings, 'screensavers': ScreensaverSettings, 'refreshes': RefreshSettings}
    before = {name: dict(getattr(viewer_settings, name)) for name in WINDOW_FIELDS}
    layouts_before = dict(window_layouts.windows)

    def undo():
        for name, value in before.items():
            setattr(viewer_settings, name, value)
        viewer_settings.save()
        window_layouts.replace_all(layouts_before)

    try:
        fresh = {name: dict(before[name]) for name in WINDOW_FIELDS}
        layouts = dict(layouts_before)
        for screen, state in windows.items():
            if not isinstance(state, dict) or not _SCREEN_NAME.fullmatch(screen):
                continue
            key = f'{viewer_id}|{screen}'
            for name in WINDOW_FIELDS:
                fresh[name].pop(key, None)              # the node's own state replaces what this one had for that screen
            layouts.pop(key, None)
            for name, model in models.items():
                if name in state:
                    try:
                        fresh[name][key] = model.model_validate(state[name])
                    except ValidationError:
                        logger.warning('Window {}: the {} that arrived are not valid, left out', screen, name)
            if isinstance(state.get('audio_outputs'), str) and 0 < len(state['audio_outputs']) <= 300:
                fresh['audio_outputs'][key] = state['audio_outputs']
            if valid_language(state.get('languages')):
                fresh['languages'][key] = state['languages']
            for name, limit in (('orientations', 4), ('flips', 3)):
                value = state.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < limit:
                    fresh[name][key] = value
            layout = state.get('layout')
            if isinstance(layout, dict):
                layout = dict(layout)
                panes = layout.get('panes') if isinstance(layout.get('panes'), dict) else {}
                layout['panes'] = {pane: id_map[pid] for pane, pid in panes.items() if pid in id_map}
                try:
                    layouts[key] = WindowLayout.model_validate(layout)
                except ValidationError:
                    logger.warning('Window {}: the layout that arrived is not valid, left out', screen)
        for name in WINDOW_FIELDS:
            setattr(viewer_settings, name, fresh[name])
        viewer_settings.save()
        window_layouts.replace_all(layouts)
    except BaseException:
        undo()
        raise
    return undo


async def apply_bundle(manifest: dict, staged: Path, viewer_id: str):
    """Make what arrived part of this node, all of it or nothing: files first (a stray file is harmless), then the
    playlists, then which windows play which. Anything that goes wrong undoes what this call did."""
    from api.ws.endpoints import WS
    uploads = upload_manager.folder
    uploads.mkdir(parents=True, exist_ok=True)
    name_map: dict[str, str] = {}
    moved: list[Path] = []
    created: list[str] = []
    vm = _vm()
    try:
        for entry in manifest['files']:
            name, src, existing = entry['name'], staged / entry['name'], uploads / entry['name']
            if (existing.is_file() and existing.stat().st_size == entry['size']
                    and await asyncio.to_thread(transfer.sha256_file, existing) == entry['sha256']):
                name_map[name] = name              # the same file is here already: both playlists use it
                continue
            if not src.is_file():
                raise TransferError(f'{name} was not received')
            dest = existing if not existing.exists() else upload_manager.create_filename(name)
            await asyncio.to_thread(shutil.move, str(src), str(dest))
            moved.append(dest)
            name_map[name] = dest.name             # a different file with the same name: it gets another one

        id_map: dict[str, str] = {}
        for original in manifest['playlists']:
            data = copy.deepcopy(original)
            pid = data['playlist_id']
            if pid in playlists_manager.playlists:
                pid = data['playlist_id'] = os.urandom(8).hex()
            for asset in data['assets']:
                if isinstance(asset.get('url'), str) and asset['url'].startswith('file:'):
                    asset['url'] = 'file:' + name_map.get(asset['url'].removeprefix('file:'), asset['url'].removeprefix('file:'))
            am = AssetsManager.model_validate(data)
            am._filepath = playlists_manager._playlist_path(pid)
            am.save()
            playlists_manager._playlists[pid] = am
            created.append(pid)
            id_map[original['playlist_id']] = pid
        playlists_manager._save_index()
        for pid in created:
            am = playlists_manager.playlists[pid]
            _bind(am)
            if vm is not None:
                vm.add_playlist_loop(am)
        if vm is not None:
            for screen, original in manifest['assignments']:
                if original in id_map:
                    vm.assign_screen(viewer_id, screen, id_map[original])
        undo_windows = apply_windows(manifest.get('windows') or {}, viewer_id, id_map)
    except BaseException:
        for pid in created:
            am = playlists_manager._playlists.pop(pid, None)
            if vm is not None:
                vm.remove_playlist_loop(pid)
            if am is not None and am._filepath:
                am._filepath.unlink(missing_ok=True)
        if created:
            playlists_manager._save_index()
        for path in moved:
            path.unlink(missing_ok=True)
        raise
    await WS.broadcast('Scheduler/playlists', playlists=playlists_manager.serialize())
    if vm is not None:
        vm.windows_arrived(viewer_id)           # the windows get what came with them (audio, keyboard, language, layout...)
        from utils.remote import viewers
        await viewers.publish(vm)
    logger.success('Received {} playlists and {} files', len(created), len(moved))


async def remove_handed_over(plan: Plan, viewer_id: str, idle: bool = True):
    """The other node has everything: drop here what only it was using (see plan_leave for what that is)."""
    from api.ws.endpoints import WS
    vm = _vm()
    if vm is not None:
        vm.drop_viewer_assignments(viewer_id, idle)
        vm.drop_viewer_settings(viewer_id)
    for pid in plan.remove_playlists:
        try:
            playlists_manager.delete(pid)
        except (ValueError, KeyError):
            continue                                # the default one or already gone: it stays
        if vm is not None:
            vm.remove_playlist_loop(pid)
    for name in plan.remove_files:
        upload_manager.remove(name)
    await WS.broadcast('Scheduler/playlists', playlists=playlists_manager.serialize())
    if vm is not None:
        from utils.remote import viewers
        await viewers.publish(vm)
    logger.info('Removed {} playlists and {} files that were handed over', len(plan.remove_playlists), len(plan.remove_files))


async def remove_everything():
    """This node joined a server and everything it had is there now: an empty default playlist is all that is left (a
    node cannot be without one). The windows are not told anything: the server is already driving them."""
    from api.ws.endpoints import WS
    plan = plan_everything()
    keep = playlists_manager.default.playlist_id
    plan.remove_playlists.discard(keep)
    await remove_handed_over(plan, LOCAL_VIEWER, idle=False)
    default = playlists_manager.default
    default.assets.clear()
    default.save()
    default.on_assets_update()
    await WS.broadcast('Scheduler/playlists', playlists=playlists_manager.serialize())


def is_empty() -> bool:
    """Nothing worth moving: no files, no playlist with anything in it, and no window that has been set up"""
    return (not any(am.assets for am in playlists_manager.playlists.values()) and not upload_manager.files
            and not collect_windows(LOCAL_VIEWER))
