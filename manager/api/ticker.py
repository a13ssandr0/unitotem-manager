"""
`Ticker/*`: the RSS sources (global) and the bar of each window (per window). All of it is panel configuration: admin.

Pushes are events: a change of the sources goes to every connected administrator (`Ticker/sources`, the list carries
addresses that may hold credentials, so it never goes to anybody else), a change of a window's bar goes to that window
(`SetTicker`, `TickerItems`) and to the administrators (`Ticker/settings`).
"""
import asyncio
import time
from typing import Optional

from loguru import logger
from pydantic import ValidationError

import utils.ticker as ticker
from api.ws.endpoints import WS
from api.ws.responses import WSResponse
from api.ws.wsmanager import WSAPIBase
from utils.models.ticker import TickerSettings, TickerSource, window_key
from utils.models.user import UserPerms, user_manager
from utils.ticker import push
from webview.controller import WebviewManager


def _vm() -> WebviewManager:
    return WebviewManager.get_instance()


def _first_error(error: ValidationError) -> str:
    err = error.errors()[0]
    where = '.'.join(str(p) for p in err.get('loc', ()))
    return f"{where}: {err['msg'].removeprefix('Value error, ')}" if where else err['msg'].removeprefix('Value error, ')


async def _to_admins(target: str, **kwargs):
    """Only the sessions of administrators (WS.broadcast would reach every logged in user)."""
    names = []
    for name, sockets in list(WS.active_users.items()):
        user = user_manager.get_user(name) if sockets else None
        if user is not None and user.has_perm.admin:
            names.append(name)
    if names:
        await WS.multicast(names, target, **kwargs)


async def push_sources():
    await _to_admins('Ticker/sources', sources=ticker.store().public_sources())


def _spawn(coro):
    task = asyncio.get_running_loop().create_task(coro)
    task.add_done_callback(lambda t: t.cancelled() or t.exception() is None
                           or logger.error('Ticker task failed: {!r}', t.exception()))


def start(loop: asyncio.AbstractEventLoop):
    """Starts the scheduler of the feeds on the manager's loop (called once from main.py)."""
    service = ticker.service()

    async def on_items(items):
        await push.items_changed(_vm().send_webview_command, _vm().get_webviews(), items)

    service.on_items = on_items
    service.on_status = push_sources
    loop.create_task(service.run(), name='ticker')


class Ticker(WSAPIBase):
    # ── the sources ──────────────────────────────────────────────────────

    @UserPerms.requires.admin
    def sources(self):
        """The news sources with their state (last result, items, error)."""
        return WSResponse(sources=ticker.store().public_sources())

    @UserPerms.requires.admin
    async def add_source(self, url: str, name: str = '', interval: int = 900, enabled: bool = True):
        """Adds an RSS/Atom feed (http or https). `interval` is in seconds (60 to 86400)."""
        try:
            source = ticker.store().add(TickerSource(url=url, name=name, interval=interval, enabled=enabled))
        except ValidationError as e:
            return WSResponse(error=_first_error(e))
        except ValueError as e:
            return WSResponse(error=str(e))
        ticker.service().source_added(source)
        await push_sources()

    @UserPerms.requires.admin
    async def edit_source(self, id: str, url: Optional[str] = None, name: Optional[str] = None,
                          interval: Optional[int] = None, enabled: Optional[bool] = None):
        """Changes a source; what is left out stays as it is. A new address is fetched at once."""
        store = ticker.store()
        try:
            before = store.get(id)
            after = store.edit(id, url=url, name=name, interval=interval, enabled=enabled)
        except KeyError as e:
            return WSResponse(error=str(e))
        except ValidationError as e:
            return WSResponse(error=_first_error(e))
        except ValueError as e:
            return WSResponse(error=str(e))
        ticker.service().source_changed(id, refetch=after.url != before.url or (after.enabled and not before.enabled))
        await push_sources()
        await ticker.service().items_changed()

    @UserPerms.requires.admin
    async def enable_source(self, id: str, enabled: bool):
        """Turns a source on or off without forgetting it."""
        return await self.edit_source(id, enabled=enabled)

    @UserPerms.requires.admin
    async def delete_source(self, id: str):
        try:
            ticker.store().delete(id)
        except KeyError as e:
            return WSResponse(error=str(e))
        ticker.service().source_deleted(id)
        await push_sources()
        await ticker.service().items_changed()

    @UserPerms.requires.admin
    def fetch_now(self, id: Optional[str] = None):
        """Fetches one source (or all of them) now instead of at their next time."""
        ticker.service().fetch_now(id)

    @UserPerms.requires.admin
    def preview(self):
        """The merged list, newest first (what the bars select from)."""
        return WSResponse(items=ticker.store().merged(), at=time.time())

    # ── the bar of a window ──────────────────────────────────────────────

    @staticmethod
    def _key(viewer_id: str, window_id: int) -> str:
        screen_name = _vm()._screen_of(viewer_id, window_id)
        if screen_name is None:
            raise KeyError(f'No window {window_id} on {viewer_id}')
        return window_key(viewer_id, screen_name)

    @UserPerms.requires.admin
    def settings(self, viewer_id: str, window_id: int):
        """The bar of a window."""
        try:
            key = self._key(viewer_id, window_id)
        except KeyError as e:
            return WSResponse(error=str(e))
        return WSResponse(viewer_id=viewer_id, window_id=window_id, settings=ticker.windows().get(key).model_dump())

    @UserPerms.requires.admin
    async def set_settings(self, viewer_id: str, window_id: int, settings: dict):
        """Sets the bar of a window (see TickerSettings): numbers are clamped, a field that cannot be repaired is
        refused with a message and nothing is changed."""
        try:
            key = self._key(viewer_id, window_id)
            new = TickerSettings.model_validate(settings)
        except KeyError as e:
            return WSResponse(error=str(e))
        except ValidationError as e:
            return WSResponse(error=_first_error(e))
        ticker.windows().put(key, new)
        await push.tell_window(_vm().send_webview_command, viewer_id, window_id, new, ticker.store().merged())
        await _to_admins('Ticker/settings', viewer_id=viewer_id, window_id=window_id, settings=new.model_dump())
