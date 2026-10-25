"""
Where the bar of each window is kept: INSIDE the window's layout (`WindowLayout.ticker`, `window_layouts.json` of
utils/models/layout.py, keyed `"<viewer id>|<screen name>"` like the keyboards), so the part `layouts` of a backup carries it and a
layout saved by the panes editor never loses it. This module is only the narrow door the ticker uses.
"""
from __future__ import annotations

from utils.models.layout import WindowLayouts, window_layouts as default_layouts
from utils.models.ticker import TickerSettings


class WindowTickers:
    def __init__(self, layouts: WindowLayouts = None):
        self.layouts = layouts if layouts is not None else default_layouts

    def get(self, key: str) -> TickerSettings:
        return self.layouts.get(key).ticker

    def put(self, key: str, settings: TickerSettings):
        layout = self.layouts.get(key)
        self.layouts.put(key, layout.model_copy(update={'ticker': settings}))

    def all_settings(self) -> dict[str, TickerSettings]:
        return {key: layout.ticker for key, layout in self.layouts.windows.items()}
