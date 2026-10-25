"""Settings of the RSS ticker of a window (package B of docs/layout-and-ticker.md extends this file)."""
from pydantic import BaseModel


class TickerSettings(BaseModel, validate_assignment=True):
    """The bar of one window: off unless asked for. Fields are added by the ticker package."""
    enabled: bool = False
