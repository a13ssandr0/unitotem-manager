from threading import Event, Timer, Thread

from dasbus.typing import get_native
from loguru import logger
from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop

from api.commons import SHUTDOWN_EVENT


class GLibThread(Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.loop = EventLoop()

    def run(self):
        self.loop.run()

    def stop(self):
        self.loop.quit()


class Controller:
    __instance = None
    SERVICE = 'io.github.a13ssandr0.unitotem'
    OBJECT = '/io/github/a13ssandr0/unitotem/WebView'
    INTERFACE = 'io.github.a13ssandr0.unitotem.WebView'
    _connected = Event()
    _show_timer: Timer | None = None

    @classmethod
    def get_instance(cls):
        if cls.__instance is None:
            cls.__instance = cls()
        return cls.__instance

    def __on_name_owner(self, name, old, new):
        if name == self.SERVICE:
            if new:
                logger.info("WebView connected")
                self._connected.set()
            elif old:
                logger.info("WebView disconnected")
                self._connected.clear()

    def __init__(self):
        self.loop_thread = GLibThread()
        self.bus = SessionMessageBus()
        self.proxy = self.bus.get_proxy(self.SERVICE, self.OBJECT, self.INTERFACE)

        self.name_proxy = self.bus.get_proxy('org.freedesktop.DBus', '/org/freedesktop/DBus')
        self.name_proxy.NameOwnerChanged.connect(self.__on_name_owner)

        self.loop_thread.start()

    def __del__(self):
        self.loop_thread.stop()

    def Show(self, src, container, fit, bg_color):
        if self._show_timer:
            self._show_timer.cancel()

        if self._connected.is_set():
            return self.proxy.Show(src, container, fit, bg_color, timeout=1)
        elif not SHUTDOWN_EVENT.is_set():
            self._show_timer = Timer(2.0, self.Show, (src, container, fit, bg_color))
            self._show_timer.start()
        return None

    def GetAllDisplays(self) -> list:
        if self._connected.is_set():
            return get_native(self.proxy.GetAllDisplays())
        return []

    def GetGPUFeatureStats(self) -> dict | None:
        if self._connected.is_set():
            return self.proxy.GetGPUFeatureStats()
        return None

    @property
    def bounds(self) -> dict | None:
        if self._connected.is_set():
            return get_native(self.proxy.Bounds)
        return None

    @bounds.setter
    def bounds(self, value: dict):
        if self._connected.is_set():
            self.proxy.Bounds = value

    @property
    def orientation(self) -> int | None:
        if self._connected.is_set():
            return self.proxy.Orientation
        return None

    @orientation.setter
    def orientation(self, value: int):
        if self._connected.is_set():
            self.proxy.Orientation = value

    @property
    def flip(self) -> int | None:
        if self._connected.is_set():
            return self.proxy.Flip
        return None

    @flip.setter
    def flip(self, value: int):
        if self._connected.is_set():
            self.proxy.Flip = value

    @property
    def allowInsecureCerts(self) -> bool | None:
        if self._connected.is_set():
            return self.proxy.AllowInsecureCerts
        return None

    @allowInsecureCerts.setter
    def allowInsecureCerts(self, value: bool):
        if self._connected.is_set():
            self.proxy.AllowInsecureCerts = value

    def Reset(self):
        if self._connected.is_set():
            return self.proxy.Reset()
        return None

    @property
    def connected(self):
        return self._connected.is_set()
