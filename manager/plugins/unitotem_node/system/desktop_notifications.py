"""
The classic desktop notifications (org.freedesktop.Notifications) as a source for the notification center.

UniTotem owns the well-known name on the buses it can reach, so whatever on the machine says "notify-send"
(an application in the X session, a service with a D-Bus client, udisks, ...) ends up in the bell instead of being lost:
a kiosk has no notification daemon and nobody is looking at the screen. Two buses are served, each one only if the name is free
(a desktop that already has its own daemon keeps it):

* the user (session) bus of the manager's own account, where desktop applications send them;
* the system bus, where services that run without a session send them (needs the policy file shipped with the package).

What is implemented, against the specification (specifications.freedesktop.org/notification, version 1.2; there is no reference
implementation to copy, the spec is the reference):

* `Notify`: summary -> title, body -> text with the markup removed (the spec says a server without markup support filters the tags,
  so `body-markup` is NOT advertised), the application name -> source, audience `admin` (the text comes from outside and can say
  anything about the machine). A non-zero `replaces_id` is returned as it is and replaces atomically; 0 gives a new id, never 0.
* `expire_timeout` (ms): -1 = the level's default pop-up time, 0 = until clicked, > 0 = that long. Urgency `critical` (level
  `error`) never expires ("critical notifications should not automatically expire"). When the time is over the sender gets
  `NotificationClosed` (1) and the id is no longer valid; the notification stays in the bell (the `persistence` capability) unless
  the `transient` hint says otherwise. The `resident` hint keeps it after an action. The de-facto hint `value` (0-100) is the progress bar.
* `actions`: advertised (`actions`). A click on the notification in the web UI invokes `default` (when the sender offered it), the other
  actions are buttons; `ActionInvoked` goes to the sender and, unless `resident`, the notification is closed (`NotificationClosed` 2).
* `CloseNotification` removes it (`NotificationClosed` 3); an id that does not exist is a D-Bus error, as the spec says.
* Not implemented, and so not advertised: markup, hyperlinks, images, icons, sounds, `ActivationToken`.

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
from sdbus.exceptions import DbusFailedError
from loguru import logger
from sdbus import DbusInterfaceCommonAsync, dbus_method_async, dbus_signal_async

import utils.constants as const
from utils.notifications import DEFAULT_TIMEOUT, Notification, notifications

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


def _hint(hints: dict, name: str):
    """The value of a hint (a D-Bus variant is a (signature, value) pair), None if it is not there."""
    entry = hints.get(name)
    return entry[1] if isinstance(entry, tuple) and len(entry) == 2 else None


def level_of(hints: dict) -> str:
    return 'error' if _hint(hints, 'urgency') == 2 else 'info'


def timeout_of(hints: dict, expire_timeout: int) -> Optional[int]:
    """The pop-up time in ms for the center: None = the level's default, 0 = until clicked."""
    if _hint(hints, 'urgency') == 2:
        return 0            # critical: never expires
    if expire_timeout == 0:
        return 0
    return expire_timeout if expire_timeout > 0 else None


def progress_of(hints: dict) -> Optional[float]:
    value = _hint(hints, 'value')       # the de-facto progress hint used by the common notification daemons
    return float(value) if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100 else None


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
        self._timers: dict[int, asyncio.TimerHandle] = {}

    # ── what an application calls ────────────────────────────────────────────

    @dbus_method_async('susssasa{sv}i', 'u', method_name='Notify')
    async def notify(self, app_name: str, replaces_id: int, app_icon: str, summary: str, body: str,
                     actions: list[str], hints: dict, expire_timeout: int) -> int:
        return self.raise_notification(app_name, replaces_id, summary, body, hints, actions, expire_timeout)

    @dbus_method_async('u', method_name='CloseNotification')
    async def close_notification(self, notification_id: int) -> None:
        key = self._known.pop(notification_id, None)
        if key is None:
            raise DbusFailedError('No such notification')        # the spec: an error when it no longer exists
        self._cancel_timer(notification_id)
        notifications.resolve(key)
        self.notification_closed.emit((notification_id, CLOSED_BY_CALL))

    @dbus_method_async(result_signature='as', method_name='GetCapabilities')
    async def get_capabilities(self) -> list[str]:
        return ['actions', 'body', 'persistence']

    @dbus_method_async(result_signature='ssss', method_name='GetServerInformation')
    async def get_server_information(self) -> tuple[str, str, str, str]:
        return 'UniTotem', 'UniTotem', const.__version__, '1.2'

    @dbus_signal_async('us', signal_name='ActionInvoked')
    def action_invoked(self) -> tuple[int, str]:
        raise NotImplementedError

    @dbus_signal_async('uu', signal_name='NotificationClosed')
    def notification_closed(self) -> tuple[int, int]:
        raise NotImplementedError

    # ── the mapping (no D-Bus in here, so it can be tested) ──────────────────

    def raise_notification(self, app_name: str, replaces_id: int, summary: str, body: str, hints: dict,
                           actions: Optional[list] = None, expire_timeout: int = -1) -> int:
        hints = hints or {}
        app = (app_name or 'application').strip()[:40] or 'application'
        if not self._allowed(app):
            # the application still gets an id, as it would from any daemon, but nothing is raised
            return self._allocate(None)
        if replaces_id:
            nid = replaces_id           # the spec: a non-zero replaces_id is returned as it is, whether we knew it or not
            self._cancel_timer(nid)
            key = self._known.get(nid) or f'fdo.{self.bus_name}.{nid}'
            self._known.pop(nid, None)
        else:
            nid = self._allocate(None)
            key = f'fdo.{self.bus_name}.{nid}'
        self._known[nid] = key
        self._forget_old()
        level = level_of(hints)
        timeout = timeout_of(hints, expire_timeout)
        pairs = [list(actions[i:i + 2]) for i in range(0, len(actions or []) - 1, 2)]
        title = summary.strip() or app
        notifications.notify(key, level, title, plain_text(body), source=app, audience='admin', timeout=timeout,
                             actions=pairs, resident=bool(_hint(hints, 'resident')), progress=progress_of(hints))
        shown_for = DEFAULT_TIMEOUT[level] if timeout is None else timeout
        if shown_for > 0:
            self._start_timer(nid, key, shown_for / 1000, transient=bool(_hint(hints, 'transient')))
        return nid

    def _start_timer(self, nid: int, key: str, seconds: float, transient: bool):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                      # not on the event loop (a test): nothing expires by itself
        self._timers[nid] = loop.call_later(seconds, self._expire, nid, key, transient)

    def _cancel_timer(self, nid: int):
        timer = self._timers.pop(nid, None)
        if timer is not None:
            timer.cancel()

    def _expire(self, nid: int, key: str, transient: bool):
        """The pop-up time is over: the sender is told, the id stops being valid, the bell keeps it (unless it is transient)."""
        self._timers.pop(nid, None)
        if self._known.get(nid) != key:
            return
        self._known.pop(nid, None)
        if transient:
            notifications.resolve(key)
        else:
            notifications.expire(key)
        self.notification_closed.emit((nid, CLOSED_EXPIRED))

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

    def action_in_center(self, key: Optional[str], action: str):
        nid = self._nid_of(key)
        if nid is not None:
            self.action_invoked.emit((nid, action))

    def _nid_of(self, key: Optional[str]) -> Optional[int]:
        if not key or not key.startswith(f'fdo.{self.bus_name}.'):
            return None
        return next((i for i, k in self._known.items() if k == key), None)

    def closed_in_center(self, key: Optional[str], dismissed: bool):
        if not key or not key.startswith(f'fdo.{self.bus_name}.'):
            return
        nid = self._nid_of(key)
        if nid is None:
            return
        if not dismissed:
            return                                # resolved by our own CloseNotification: already answered
        self._cancel_timer(nid)
        self._known.pop(nid, None)
        self.notification_closed.emit((nid, CLOSED_DISMISSED))


_servers: list[Server] = []


def _action(item: Notification, action: str):
    for server in _servers:
        server.action_in_center(item.key, action)


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
    notifications.listen_action(_action)
    for bus_name, opener in (('user', sdbus.sd_bus_open_user), ('system', sdbus.sd_bus_open_system)):
        server = await _serve_on(bus_name, opener)
        if server is not None:
            _servers.append(server)
    if not _servers:
        return
    await asyncio.Event().wait()
