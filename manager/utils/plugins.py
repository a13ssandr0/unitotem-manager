"""
Optional parts of UniTotem, installed as separate packages.

The core (this directory) is a complete orchestrator on its own: scheduler, uploads, users, remote control of other
nodes, backup and the web interface. Everything that touches the machine it runs on lives in a plugin:

- `unitotem_node`   network, audio, displays and screen power, power buttons, timers, updates, terminal, hotspot
- `unitotem_viewer` the local fullscreen windows (Qt + CEF)

A plugin is a Python package in the `plugins/` directory beside the core, exposing a `PLUGIN` object (below). Installing
a package adds its directory, removing it takes the feature away, and the web interface hides what the server does not
offer (see `canCall` in webui/src/api.js). A plugin that fails to load is logged as an error and left out: the core must
keep serving even when an optional part is broken.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence, Union

from fastapi import APIRouter
from loguru import logger

from utils.notifications import notifications

PLUGINS_DIR = Path(__file__).resolve().parent.parent / 'plugins'

Maybe = Union[None, Awaitable[None]]


@dataclass
class BackupPart:
    """One selectable piece of the configuration, for backup, restore and reset.

    `export` returns something JSON-serialisable; `restore` receives it back together with the version that wrote it;
    `reset` returns the piece to factory defaults. Pieces made of files (the uploads) use `export_files` /
    `restore_files` on the zip instead.
    """
    key: str
    label: str
    export: Optional[Callable[[], Any]] = None
    restore: Optional[Callable[[Any, str], Maybe]] = None
    reset: Optional[Callable[[], Maybe]] = None
    export_files: Optional[Callable[[Any], Maybe]] = None       # (ZipFile)
    restore_files: Optional[Callable[[Any], Maybe]] = None      # (ZipFile)
    # Large or slow to produce, so not part of a backup unless asked for
    optional_in_backup: bool = False


@dataclass
class Plugin:
    name: str
    # (module, class, prefix) as taken by WebSocketAPI.import_class
    api: Sequence[tuple[str, str, Optional[str]]] = ()
    # Returns the routers to include, before the core's catch-all route and in this order. A function, not the routers
    # themselves: they usually import the core's login, which itself needs the API that is being built while plugins load.
    routers: Optional[Callable[[], Sequence[APIRouter]]] = None
    backup: Sequence[BackupPart] = ()
    # called once the event loop exists and the core's managers are loaded; may create tasks on `loop`
    on_startup: Optional[Callable[[Any], None]] = None
    # the very first boot, before anything is configured (the hotspot that lets someone in)
    on_first_boot: Optional[Callable[[], Awaitable[None]]] = None
    # extra context for the first-boot page, e.g. the hotspot's SSID and QR code
    first_boot_context: Optional[Callable[[], Awaitable[dict]]] = None
    on_shutdown: Optional[Callable[[], Awaitable[None]]] = None
    # The local windows. Runs on the main thread and blocks until the program has to end; `request_quit` can be called
    # from any thread to make it return. At most one plugin provides this.
    run_gui: Optional[Callable[[Callable[[], None]], None]] = None
    request_quit: Optional[Callable[[], None]] = None
    # Extra facts the core may ask for: audio_outputs() -> list of {'name', 'description'}
    audio_outputs: Optional[Callable[[], list[dict]]] = None


loaded: list[Plugin] = []


def load(directory: Path = PLUGINS_DIR) -> list[Plugin]:
    """Imports every plugin package found in `directory`. Safe to call once, at startup."""
    if not directory.is_dir():
        return loaded
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    for info in sorted(pkgutil.iter_modules([str(directory)]), key=lambda i: i.name):
        if not info.ispkg or any(p.name == info.name for p in loaded):
            continue
        try:
            plugin = importlib.import_module(info.name).PLUGIN
            if not isinstance(plugin, Plugin):
                raise TypeError('PLUGIN is not a utils.plugins.Plugin')
        except Exception:
            logger.exception('Plugin {} failed to load, continuing without it', info.name)
            notifications.notify(f'plugin.{info.name}', 'error', f'Part "{info.name}" did not start',
                                 'It failed to load and the node runs without it. The log has the details.',
                                 source='plugins', audience='admin')
            continue
        notifications.resolve(f'plugin.{info.name}')
        loaded.append(plugin)
        logger.info('Loaded plugin {}', plugin.name)
    return loaded


def provides(name: str) -> bool:
    return any(p.name == name for p in loaded)


def each(attribute: str):
    """The non-empty `attribute` of every loaded plugin."""
    for plugin in loaded:
        value = getattr(plugin, attribute)
        if value:
            yield plugin, value
