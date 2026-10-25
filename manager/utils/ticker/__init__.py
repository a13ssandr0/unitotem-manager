"""
The RSS ticker of the windows (core: runs on a plain Debian server, imports no plugin).

`store`/`service` are created lazily because building them reads the configuration directory: importing this
package costs nothing and a test can build its own with `TickerStore(tmp_path / ...)`.
"""
from __future__ import annotations

from typing import Optional

from utils.ticker.service import TickerService
from utils.ticker.store import TickerStore
from utils.ticker.windows import WindowTickers

_store: Optional[TickerStore] = None
_service: Optional[TickerService] = None
_windows: Optional[WindowTickers] = None


def store() -> TickerStore:
    global _store
    if _store is None:
        _store = TickerStore()
    return _store


def windows() -> WindowTickers:
    global _windows
    if _windows is None:
        _windows = WindowTickers()
    return _windows


def service() -> TickerService:
    global _service
    if _service is None:
        from utils.models.viewer import viewer_settings
        _service = TickerService(store(), insecure=lambda: viewer_settings.allow_insecure_certs)
    return _service
