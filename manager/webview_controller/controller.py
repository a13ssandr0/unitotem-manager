from threading import Event, Timer, Thread
from time import sleep
from xml.etree import ElementTree as xml

from dasbus.client.proxy import InterfaceProxy
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

    __current = {}

    @classmethod
    def get_instance(cls):
        if cls.__instance is None:
            cls.__instance = cls()
        return cls.__instance

    def __on_name_owner(self, name, old, new):
        if name == self.SERVICE:
            if new:
                logger.success("WebView connected")
                self.__query_windows()
                self._connected.set()
                if 0 in self.__current:
                    self.Show(*self.__current[0])
            elif old:
                logger.warning("WebView disconnected")
                self._connected.clear()

    def __query_windows(self):
        _xml = xml.fromstring(self.bus.get_proxy(self.SERVICE, self.OBJECT+'/Window',
                                                 'org.freedesktop.DBus.Introspectable').Introspect())
        _windows = [e.attrib['name'] for e in _xml]

        self.window_proxies = {int(name): self.bus.get_proxy(self.SERVICE, self.OBJECT + '/Window/' + name,
                                                  'io.github.a13ssandr0.unitotem.WebView.Window') for name in _windows}

    def __init__(self):
        self.loop_thread = GLibThread()
        self.bus = SessionMessageBus()
        self.proxy = self.bus.get_proxy(self.SERVICE, self.OBJECT, self.INTERFACE)
        self.window_proxies = []

        self.name_proxy = self.bus.get_proxy('org.freedesktop.DBus', '/org/freedesktop/DBus')
        self.name_proxy.NameOwnerChanged.connect(self.__on_name_owner)

        if self.SERVICE in self.name_proxy.ListNames():
            self.__query_windows()
            self._connected.set()

        self.loop_thread.start()

    def __del__(self):
        self.loop_thread.stop()

    def Show(self, src, container, fit, bg_color):
        self.__current[0] = (src, container, fit, bg_color)
        if self._connected.is_set():
            logger.info("Showing {} [container={}; fit={}; bg_color={}]", src, container, fit, bg_color)
            try:
                return self.window_proxies[0].Show(src, container, fit, bg_color, timeout=1000)
            except TimeoutError:
                logger.warning("WebView Show timed out")
        return None

    def GetAllDisplays(self) -> list:
        if self._connected.is_set():
            return get_native(self.proxy.GetAllDisplays(timeout=1000))
        return []

    def GetGPUFeatureStats(self) -> dict | None:
        if self._connected.is_set():
            return self.proxy.GetGPUFeatureStats(timeout=1000)
        return None

    class Window:
        def __class_getitem__(cls, item):
            return cls(Controller.get_instance().window_proxies[item], Controller.get_instance()._connected)

        def __init__(self, proxy: 'InterfaceProxy', connected: 'Event'):
            self.proxy = proxy
            self._connected = connected

        @property
        def bounds(self) -> dict | None:
            if self._connected.is_set():
                return get_native(self.proxy.Bounds)
            return None

        @bounds.setter
        def bounds(self, value: dict):
            if self._connected.is_set():
                # noinspection PyDunderSlots,PyUnresolvedReferences
                self.proxy.Bounds = value

        @property
        def orientation(self) -> int | None:
            if self._connected.is_set():
                return self.proxy.Orientation
            return None

        @orientation.setter
        def orientation(self, value: int):
            if self._connected.is_set():
                # noinspection PyDunderSlots,PyUnresolvedReferences
                self.proxy.Orientation = value

        @property
        def flip(self) -> int | None:
            if self._connected.is_set():
                return self.proxy.Flip
            return None

        @flip.setter
        def flip(self, value: int):
            if self._connected.is_set():
                # noinspection PyDunderSlots,PyUnresolvedReferences
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
            return self.proxy.Reset(timeout=1000)
        return None

    @property
    def connected(self):
        return self._connected.is_set()
