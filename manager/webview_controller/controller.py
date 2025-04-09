from functools import cache
from threading import Timer

from gi.repository import GLib
from loguru import logger
from pydbus import SessionBus

from api.commons import SHUTDOWN_EVENT

show_timer: Timer | None = None
connected = None


@cache
def _get_proxy():
    bus = SessionBus()
    return bus.get('unitotem.WebView', '/unitotem/WebView', timeout=1)


def get_proxy():
    try:
        return _get_proxy()
    except GLib.GError:
        logger.error('Failed to connect to WebView, will try again later')
        return None


class Controller:
    # noinspection PyPep8Naming
    @classmethod
    def Show(cls, src, container, fit, bg_color):
        global connected, show_timer
        if show_timer:
            show_timer.cancel()
        try:
            proxy = _get_proxy()
            connected = True
            return proxy.Show(src, container, fit, bg_color)
        except GLib.GError:
            if not SHUTDOWN_EVENT.is_set():
                if connected or connected is None: logger.trace('Not connected to WebView, will retry every 2 seconds')
                show_timer = Timer(2.0, cls.Show, (src, container, fit, bg_color))
                show_timer.start()
                connected = False

    # noinspection PyPep8Naming
    @staticmethod
    def GetAllDisplays() -> list:
        if proxy := get_proxy():
            return proxy.GetAllDisplays()
        return []

    @property
    def bounds(self) -> dict | None:
        if proxy := get_proxy():
            return proxy.bounds

    @bounds.setter
    def bounds(self, value: dict):
        if proxy := get_proxy():
            proxy.bounds = value

    @property
    def orientation(self) -> int | None:
        if proxy := get_proxy():
            return proxy.orientation

    @orientation.setter
    def orientation(self, value: int):
        if proxy := get_proxy():
            proxy.orientation = value

    @property
    def flip(self) -> int | None:
        if proxy := get_proxy():
            return proxy.flip()

    @flip.setter
    def flip(self, value: int):
        if proxy := get_proxy():
            proxy.flip = value

    # noinspection PyPep8Naming
    @property
    def allowInsecureCerts(self) -> bool | None:
        if proxy := get_proxy():
            return proxy.allowInsecureCerts

    # noinspection PyPep8Naming
    @allowInsecureCerts.setter
    def allowInsecureCerts(self, value: bool):
        if proxy := get_proxy():
            proxy.allowInsecureCerts = value

    # noinspection PyPep8Naming
    def Reset(self):
        if proxy := get_proxy():
            return proxy.Reset()


controller = Controller()
