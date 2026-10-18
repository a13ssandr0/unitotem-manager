"""
The classic desktop notifications (org.freedesktop.Notifications) as a source for the notification center.

UniTotem owns the well-known name on the buses it can reach, so whatever on the machine says "notify-send"
(an application in the X session, a service with a D-Bus client, udisks, ...) ends up in the bell instead of being lost:
a kiosk has no notification daemon and nobody is looking at the screen. Two buses are served, each one only if the name is free
(a desktop that already has its own daemon keeps it):

* the user (session) bus of the manager's own account, where desktop applications send them;
* the system bus, where services that run without a session send them (needs the policy file shipped with the package).

The mapping is deliberately small: summary -> title, body -> text with the markup removed, the application name -> source,
urgency `critical` -> level `error` (anything else is `info`), audience `admin` (the text comes from outside and can say
anything about the machine). Actions, icons, images and sounds are ignored (the server advertises only `body` and
`persistence`). A notification that replaces an earlier one (`replaces_id`) updates it. `CloseNotification` removes it;
dismissing it in the web UI answers the application with `NotificationClosed` (reason 2), as a daemon would.

A noisy sender is limited per application (`RATE_LIMIT` per `RATE_WINDOW` seconds): the center is bounded and a flood must not push the
notifications that matter out of it.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from collections import defaultdict, deque
from typing import Optional

import sdbus
from loguru import logger
from sdbus import DbusInterfaceCommonAsync, dbus_method_async, dbus_signal_async

from utils.notifications import Notification, notifications

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
RATE_LIMIT = 20
RATE_WINDOW = 60.0
MAX_KNOWN = 500                  # ids remembered per bus: more than the center keeps, so an old id still replaces
CLOSED_EXPIRED, CLOSED_DISMISSED, CLOSED_BY_CALL = 1, 2, 3
_TAGS = re.compile(r'<[^>]*>')


def plain_text(body: str) -> str:
    """The body of a notification may carry a tiny subset of HTML (<b>, <i>, <u>, <a>, <img>): the center shows plain text."""
    return html.unescape(_TAGS.sub('', body or ''))


def level_of(hints: dict) -> str:
    urgency = hints.get('urgency')
    value = urgency[1] if isinstance(urgency, tuple) and len(urgency) == 2 else None
    return 'error' if value == 2 else 'info'


class Server(DbusInterfaceCommonAsync, interface_name=NAME):
    """One instance per bus. Ids are per bus; the keys in the center say which bus they came from."""

    def __init__(self, bus_name: str, now=time.monotonic):
        super().__init__()
        self.bus_name = bus_name
        self._now = now
        self._next_id = 0
        self._known: dict[int, str] = {}                 # id -> key in the center, in order of creation
        self._sent: dict[str, deque] = defaultdict(deque)   # application -> times of its recent notifications
        self._warned: set[str] = set()

    # ── what an application calls ────────────────────────────────────────────

    @dbus_method_async('susssasa{sv}i', 'u', method_name='Notify')
    async def notify(self, app_name: str, replaces_id: int, app_icon: str, summary: str, body: str,
                     actions: list[str], hints: dict, expire_timeout: int) -> int:
        return self.raise_notification(app_name, replaces_id, summary, body, hints)

    @dbus_method_async('u', method_name='CloseNotification')
    async def close_notification(self, notification_id: int) -> None:
        key = self._known.pop(notification_id, None)
        if key is not None:
            notifications.resolve(key)
        self.notification_closed.emit((notification_id, CLOSED_BY_CALL))

    @dbus_method_async(result_signature='as', method_name='GetCapabilities')
    async def get_capabilities(self) -> list[str]:
        return ['body', 'persistence']

    @dbus_method_async(result_signature='ssss', method_name='GetServerInformation')
    async def get_server_information(self) -> tuple[str, str, str, str]:
        return 'UniTotem', 'UniTotem', '1', '1.2'

    @dbus_signal_async('uu', signal_name='NotificationClosed')
    def notification_closed(self) -> tuple[int, int]:
        raise NotImplementedError

    # ── the mapping (no D-Bus in here, so it can be tested) ──────────────────

    def raise_notification(self, app_name: str, replaces_id: int, summary: str, body: str, hints: dict) -> int:
        app = (app_name or 'application').strip()[:40] or 'application'
        if not self._allowed(app):
            # the application still gets an id, as it would from any daemon, but nothing is raised
            return self._allocate(None)
        if replaces_id in self._known:
            nid = replaces_id
            key = self._known[nid]
        else:
            nid = self._allocate(None)
            key = f'fdo.{self.bus_name}.{nid}'
            self._known[nid] = key
            self._forget_old()
        title = summary.strip() or app
        notifications.notify(key, level_of(hints or {}), title, plain_text(body), source=app, audience='admin')
        return nid

    def _allocate(self, key: Optional[str]) -> int:
        self._next_id = self._next_id % 0xFFFFFFFF + 1       # never 0: it means "no replacement" to the caller
        if key is not None:
            self._known[self._next_id] = key
        return self._next_id

    def _forget_old(self):
        while len(self._known) > MAX_KNOWN:
            self._known.pop(next(iter(self._known)))

    def _allowed(self, app: str) -> bool:
        times, now = self._sent[app], self._now()
        while times and now - times[0] > RATE_WINDOW:
            times.popleft()
        if len(times) >= RATE_LIMIT:
            if app not in self._warned:           # once per application, not once per notification
                self._warned.add(app)
                logger.warning('Notifications from {!r} are limited to {} per {:.0f} s', app, RATE_LIMIT, RATE_WINDOW)
            return False
        self._warned.discard(app)
        times.append(now)
        if len(self._sent) > 200:                 # applications that went quiet
            for name in [n for n, t in self._sent.items() if not t or now - t[-1] > RATE_WINDOW]:
                del self._sent[name]
        return True

    # ── the web UI dismissed or the producer resolved one ────────────────────

    def closed_in_center(self, key: Optional[str], dismissed: bool):
        if not key or not key.startswith(f'fdo.{self.bus_name}.'):
            return
        nid = next((i for i, k in self._known.items() if k == key), None)
        if nid is None:
            return
        if not dismissed:
            return                                # resolved by our own CloseNotification: already answered
        self.notification_closed.emit((nid, CLOSED_DISMISSED))


_servers: list[Server] = []


def _closed(item: Notification, dismissed: bool):
    for server in _servers:
        server.closed_in_center(item.key, dismissed)


async def _serve_on(bus_name: str, open_bus) -> Optional[Server]:
    try:
        bus = open_bus()
    except Exception as exc:                      # no user bus for this account, no system bus (a container)
        logger.info('Desktop notifications: no {} bus ({})', bus_name, exc)
        return None
    server = Server(bus_name)
    try:
        server.export_to_dbus(PATH, bus)
        await bus.request_name_async(NAME, 0)     # 0: do not queue, do not replace anybody
    except Exception as exc:
        # most often the name is taken (a desktop daemon) or the system bus policy does not allow it
        logger.info('Desktop notifications: not serving the {} bus ({})', bus_name, exc)
        return None
    logger.info('Desktop notifications: serving {} on the {} bus', NAME, bus_name)
    return server


async def serve() -> None:
    """Start serving on every reachable bus. The servers live as long as the process (the buses stay open)."""
    notifications.listen_closed(_closed)
    for bus_name, opener in (('user', sdbus.sd_bus_open_user), ('system', sdbus.sd_bus_open_system)):
        server = await _serve_on(bus_name, opener)
        if server is not None:
            _servers.append(server)
    if not _servers:
        return
    await asyncio.Event().wait()
