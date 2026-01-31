from threading import Event, Timer, Thread
from time import sleep
from xml.etree import ElementTree as xml

from dasbus.client.proxy import InterfaceProxy
from dasbus.typing import get_native
from loguru import logger
from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop
from propcache import cached_property

from api.commons import SHUTDOWN_EVENT


class GLibThread(Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.loop = EventLoop()

    def run(self):
        self.loop.run()

    def stop(self):
        self.loop.quit()


class WindowProxy:
    def __init__(self, proxy: 'InterfaceProxy', connected: 'Event'):
        self.proxy = proxy
        self._connected = connected
        self.__current = None

    def Show(self, src, container, fit, bg_color):
        self.__current = (src, container, fit, bg_color)
        if self._connected.is_set():
            logger.info("Showing {} [container={}; fit={}; bg_color={}]", src, container, fit, bg_color)
            try:
                return self.proxy.Show(src, container, fit, bg_color, timeout=1000)
            except TimeoutError:
                logger.warning("WebView Show timed out")
        return None

    def show_current(self):
        if self.__current is not None:
            return self.Show(*self.__current)
        return None

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



class Controller:
    __instance = None
    SERVICE = 'io.github.a13ssandr0.unitotem'
    OBJECT = f'/{SERVICE.replace(".", "/")}/WebView'
    INTERFACE = f'{SERVICE}.WebView'

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
                self.__connected.set()
                for win in self.window_proxies:
                    self.Window[win].show_current()
            elif old:
                logger.warning("WebView disconnected")
                self.__connected.clear()

    def __query_windows(self):
        _xml = xml.fromstring(self.bus.get_proxy(self.SERVICE, self.OBJECT+'/Window',
                                                 'org.freedesktop.DBus.Introspectable').Introspect())
        for e in _xml:
            name = int(e.attrib['name'])
            if name not in self.window_proxies:
                self.window_proxies[name] = WindowProxy(
                        self.bus.get_proxy(
                                self.SERVICE,
                                f'{self.OBJECT}/Window/{name}',
                                f'{self.INTERFACE}.Window'
                        ),
                        self.__connected
                )

    def __init__(self):
        self.__connected = Event()
        self.loop_thread = GLibThread()
        self.bus = SessionMessageBus()
        self.proxy = self.bus.get_proxy(self.SERVICE, self.OBJECT, self.INTERFACE)
        self.window_proxies = []

        if self.SERVICE in self.bus.proxy.ListNames():
            self.__query_windows()
            self.__connected.set()

        self.bus.proxy.NameOwnerChanged.connect(self.__on_name_owner)

        self.loop_thread.start()

    def __del__(self):
        self.loop_thread.stop()

    def GetAllDisplays(self) -> list:
        if self.__connected.is_set():
            return get_native(self.proxy.GetAllDisplays(timeout=1000))
        return []

    def GetGPUFeatureStats(self) -> dict | None:
        if self.__connected.is_set():
            return self.proxy.GetGPUFeatureStats(timeout=1000)
        return None

    @cached_property
    def Window(self):
        return self.window_proxies

    @property
    def allowInsecureCerts(self) -> bool | None:
        if self.__connected.is_set():
            return self.proxy.AllowInsecureCerts
        return None

    @allowInsecureCerts.setter
    def allowInsecureCerts(self, value: bool):
        if self.__connected.is_set():
            self.proxy.AllowInsecureCerts = value

    def Reset(self):
        if self.__connected.is_set():
            return self.proxy.Reset(timeout=1000)
        return None

    @property
    def connected(self):
        return self.__connected.is_set()
