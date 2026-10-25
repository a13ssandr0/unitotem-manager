"""
What the windows are told, by events only: the settings of their bar (`SetTicker`) and the merged list (`TickerItems`).

A window gets both when it appears (the same payload as at any other time) and again only when its settings change
(`SetTicker`) or when a fetch changes the merged list (`TickerItems`). The page never asks. `send` is the controller's
`send_webview_command(viewer_id, target, **kwargs)`, so this module imports nothing of the viewer side.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from loguru import logger

from utils.models.ticker import TickerSettings, window_key
import utils.ticker as ticker

Send = Callable[..., Awaitable[None]]


def items_for(settings: TickerSettings, items: list[dict]) -> list[dict]:
    """The merged list as one window needs it: the summary only travels when the template shows it."""
    if '{summary' in settings.template:
        return items
    return [{**i, 'summary': ''} for i in items]


async def tell_window(send: Send, viewer_id: str, window_id: int, settings: TickerSettings, items: list[dict]):
    """The settings, and (when the bar is on) the list: what a window needs to draw its bar."""
    await send(viewer_id, 'SetTicker', window_id=window_id, **settings.for_page())
    if settings.enabled:
        await send(viewer_id, 'TickerItems', window_id=window_id, items=items_for(settings, items))


async def window_appeared(send: Send, viewer_id: str, window_id: int, screen_name):
    """A window has just been created (or the viewer reconnected): a bar that is off needs no message at all."""
    settings = ticker.windows().get(window_key(viewer_id, screen_name))
    if settings.enabled:
        await tell_window(send, viewer_id, window_id, settings, ticker.store().merged())


async def refresh_all(send: Send, webviews: dict):
    """Every window gets its settings and list again (after a restore or a reset replaced them)."""
    merged = ticker.store().merged()
    for viewer_id, info in webviews.items():
        if not info.get('layout'):
            continue        # a viewer of an older version does not know the message
        for window in info.get('windows', []):
            settings = ticker.windows().get(window_key(viewer_id, window.get('screen_name')))
            try:
                await tell_window(send, viewer_id, window['window_id'], settings, merged)
            except Exception as exc:
                logger.warning('Cannot refresh the bar of {} window {}: {!r}', viewer_id, window.get('window_id'), exc)


async def items_changed(send: Send, webviews: dict, items: list[dict]):
    """The merged list is different: every window with a bar gets it."""
    for viewer_id, info in webviews.items():
        if not info.get('layout'):
            continue
        for window in info.get('windows', []):
            settings = ticker.windows().get(window_key(viewer_id, window.get('screen_name')))
            if not settings.enabled:
                continue
            try:
                await send(viewer_id, 'TickerItems', window_id=window['window_id'], items=items_for(settings, items))
            except Exception as exc:       # one unreachable viewer must not keep the others from their list
                logger.warning('Cannot send the news list to {} window {}: {!r}', viewer_id, window.get('window_id'), exc)
