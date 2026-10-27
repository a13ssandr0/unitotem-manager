"""
The notification center: things the administrators of a node should know about and cannot be told by a dialog, because
nobody was looking when they happened (a suspend that was refused at night, an update that is available, a client that
went away).

How a producer uses it (core code and plugins alike: a plugin may import this module, never the other way round):

    from utils.notifications import notifications

    notifications.notify('suspend.refused', 'warning', 'Suspend refused', reason, source='timers',
                         audience='admin', link='/settings/timers')
    ...
    notifications.resolve('suspend.refused')        # the problem is gone: the notification goes away by itself

* `key` identifies "the same" notification. Raising it again with the same content does nothing (a periodic check can
  call `notify` every time without nagging, and a notification somebody dismissed stays dismissed); raising it with
  different content updates it and makes it unread again. `resolve(key)` removes it. Without a key every call is a new
  notification, and dismissing it removes it.
* `audience` is the capability needed to see it (`scheduler`, `audio`, `power`, `admin`), or None for every logged in
  user. An admin sees everything. Use `admin` whenever the text can reveal anything about the machine or the network.
* Text is plain text, never markup (the web UI renders it escaped), and is truncated: producers may put things other
  people chose (the name of a device that asked to be paired) in it.
* `progress` (0-100, or -1 for "working, unknown how far") turns a notification into a progress bar and `persistent` makes it
  impossible to dismiss while the work lasts: `notifications.set_progress(key, percent, text)` moves the bar without making
  the notification unread again, `resolve(key)` removes it when the work ends (the updater's download and repair).
* `timeout` is how long the page shows the notification as a pop-up, in milliseconds, with the meaning of the freedesktop
  `expire_timeout`: None = the default of its level (`DEFAULT_TIMEOUT`), 0 = until somebody clicks it, > 0 = that long. The bell keeps
  it either way (it is the notification center, the freedesktop `persistence` capability). `actions` are freedesktop (id, label) pairs:
  the one named `default` is what a click on the notification does besides following its `link`; `invoke()` runs one and
  tells the listeners (`listen_action`), after which a notification that is not `resident` is dismissed.
* The list is bounded, kept in /var/lib/unitotem (it is state, not configuration: it is not part of a backup) and
  survives a restart. The file has a version and every field is optional on reading, so the previous release can read
  what this one wrote and the other way round (rollback rule).
* Producers may run in any thread: everything is under one lock, and the push to the open pages is scheduled on the
  event loop of the manager (`bind`). A failing disk or push never propagates to a producer.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

LEVELS = ('info', 'warning', 'error')
AUDIENCES = ('scheduler', 'audio', 'power', 'admin')
MAX_ITEMS = 100
MAX_TITLE = 120
MAX_TEXT = 1000
MAX_KEY = 120
FILE_VERSION = 1
NOTIFICATIONS_FILE = Path('/var/lib/unitotem/notifications.json')
# milliseconds of pop-up by level when the producer does not say (freedesktop: "a sane expiration timeout dependent on the urgency")
DEFAULT_TIMEOUT = {'info': 6000, 'warning': 9000, 'error': 12000}
MAX_ACTIONS = 6


class Notification(BaseModel):
    # An unknown field is a newer release's: ignored on reading, and so dropped on the next write
    model_config = ConfigDict(extra='ignore')

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    key: Optional[str] = None
    level: str = 'info'
    title: str = ''
    text: str = ''
    source: str = ''
    audience: Optional[str] = None
    link: Optional[str] = None
    created: float = 0
    updated: float = 0
    read: bool = False
    dismissed: bool = False
    progress: Optional[float] = None        # 0-100, -1 = unknown, None = no bar
    persistent: bool = False                # cannot be dismissed (it is about work in progress)
    timeout: Optional[int] = None           # ms of pop-up: None = level default, 0 = until clicked
    actions: list = Field(default_factory=list)      # [id, label] pairs, only while the sender can be told (any shape is read, cleared on load)
    resident: bool = False                  # stays after an action was invoked

    def public(self) -> dict:
        return {'id': self.id, 'level': self.level, 'title': self.title, 'text': self.text, 'source': self.source,
                'audience': self.audience, 'link': self.link, 'created': self.created, 'updated': self.updated,
                'read': self.read, 'progress': self.progress, 'persistent': self.persistent,
                'timeout': self.timeout if self.timeout is not None else DEFAULT_TIMEOUT.get(self.level, 6000),
                'actions': [{'id': a[0], 'label': a[1]} for a in self.actions if a[0] != 'default'],
                'default_action': any(a[0] == 'default' for a in self.actions)}


def _clip(value, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    # control characters have no business in a notification (newlines in the text are fine)
    text = ''.join(c for c in text if c.isprintable() or c == '\n')
    return text.strip()[:limit]


def _clean_link(link: Optional[str]) -> Optional[str]:
    """Only a page of this very web UI: a path, never an address (`//host` and `/\\host` are addresses to a browser)."""
    if isinstance(link, str) and link.startswith('/') and not link.startswith(('//', '/\\')) \
            and all(c.isprintable() and c not in ' \\' for c in link) and len(link) <= 200:
        return link
    return None


def _clean_actions(actions) -> list[list[str]]:
    """(id, label) pairs, as plain strings and no more than MAX_ACTIONS."""
    out = []
    for pair in actions or []:
        try:
            ident, label = _clip(pair[0], 60), _clip(pair[1], 60)
        except (TypeError, IndexError):
            continue
        if ident and len(out) < MAX_ACTIONS:
            out.append([ident, label])
    return out


def _perms(perms: Iterable) -> set[str]:
    return {getattr(p, 'value', p) for p in perms}


def visible_to(item: Notification, perms: Iterable) -> bool:
    """An admin sees everything, anybody else what is for everybody or for a capability they have."""
    have = _perms(perms)
    return 'admin' in have or item.audience is None or item.audience in have


class NotificationCenter:
    def __init__(self, path: Path = NOTIFICATIONS_FILE, now: Callable[[], float] = time.time,
                 max_items: int = MAX_ITEMS):
        self.path = path
        self._now = now
        self.max_items = max_items
        self._lock = threading.RLock()
        self._items: list[Notification] = []
        self._loaded = False
        self._warned_disk = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._push: Optional[Callable[[], Awaitable[None]]] = None
        self._push_scheduled = False
        self._closed_listeners: list[Callable[[Notification, bool], None]] = []
        self._action_listeners: list[Callable[[Notification, str], None]] = []

    # ── storage ──────────────────────────────────────────────────────────────

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text())
            entries = raw.get('items', []) if isinstance(raw, dict) else []
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning('Cannot read the notifications ({}), starting with none', exc)
            return
        for entry in entries if isinstance(entries, list) else []:
            try:
                item = Notification.model_validate(entry)
            except ValidationError:
                continue        # one bad entry must not take the others with it
            if item.level not in LEVELS:
                item.level = 'info'
            if item.audience not in (None, *AUDIENCES):
                item.audience = 'admin'     # unknown audience (a newer release's): the safe reading is the strictest
            item.link = _clean_link(item.link)
            item.actions = []        # the sender of an action does not outlive the manager
            item.title, item.text = _clip(item.title, MAX_TITLE), _clip(item.text, MAX_TEXT)
            self._items.append(item)
        self._trim()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + '.tmp')
            with open(tmp, 'w') as f:
                json.dump({'version': FILE_VERSION, 'items': [i.model_dump() for i in self._items]}, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.path)
            self._warned_disk = False
        except OSError as exc:
            if not self._warned_disk:       # once, not on every change of a read-only disk
                logger.warning('Cannot save the notifications: {}', exc)
                self._warned_disk = True

    def _trim(self):
        """Keep at most `max_items`: what nobody can see any more goes first, then what was read, then the oldest."""
        while len(self._items) > self.max_items:
            victim = min(self._items, key=lambda i: (not i.dismissed, not i.read, i.updated))
            self._items.remove(victim)

    # ── producers ────────────────────────────────────────────────────────────

    def notify(self, key: Optional[str], level: str, title: str, text: str = '', *, source: str = '',
               audience: Optional[str] = None, link: Optional[str] = None, progress: Optional[float] = None,
               persistent: bool = False, timeout: Optional[int] = None, actions: Optional[list] = None,
               resident: bool = False) -> Optional[str]:
        """Raise a notification (see the module docstring). Returns its id, or None if it could not be raised. Never raises."""
        try:
            if level not in LEVELS:
                raise ValueError(f'unknown level {level!r}')
            if audience not in (None, *AUDIENCES):
                raise ValueError(f'unknown audience {audience!r}')
            title, text = _clip(title, MAX_TITLE), _clip(text, MAX_TEXT)
            key = _clip(key, MAX_KEY) if key else None
            source = _clip(source, 40)
            link = _clean_link(link)
            progress = None if progress is None else max(-1.0, min(100.0, float(progress)))
            timeout = None if timeout is None or timeout < 0 else min(int(timeout), 3600_000)
            actions = _clean_actions(actions)
        except Exception as exc:        # a producer's bug is logged, it must not take the producer down
            logger.error('Invalid notification {!r}: {}', title, exc)
            return None
        with self._lock:
            self._load()
            now = self._now()
            same = next((i for i in self._items if key and i.key == key), None)
            if same is not None:
                content = (level, title, text, audience, link, source, progress, persistent, timeout, actions, resident)
                if content == (same.level, same.title, same.text, same.audience, same.link, same.source, same.progress,
                               same.persistent, same.timeout, same.actions, same.resident):
                    return same.id      # the same thing again: nothing to tell
                same.level, same.title, same.text = level, title, text
                same.audience, same.link, same.source = audience, link, source
                same.progress, same.persistent = progress, persistent
                same.timeout, same.actions, same.resident = timeout, actions, resident
                same.updated, same.read, same.dismissed = now, False, False
                item = same
            else:
                item = Notification(key=key, level=level, title=title, text=text, source=source,
                                    audience=audience, link=link, created=now, updated=now,
                                    progress=progress, persistent=persistent, timeout=timeout, actions=actions,
                                    resident=resident)
                self._items.append(item)
                self._trim()
            self._changed()
            return item.id

    def set_progress(self, key: str, progress: float, text: Optional[str] = None) -> bool:
        """Move the bar of the notification `key` (and optionally its text) without making it unread again. False if there is none."""
        try:
            progress = max(-1.0, min(100.0, float(progress)))
        except (TypeError, ValueError):
            return False
        with self._lock:
            self._load()
            item = next((i for i in self._items if i.key == key), None)
            if item is None:
                return False
            text = item.text if text is None else _clip(text, MAX_TEXT)
            if (item.progress, item.text) == (progress, text):
                return True
            item.progress, item.text, item.updated = progress, text, self._now()
            self._changed()
        return True

    def resolve(self, key: str) -> bool:
        """The problem behind `key` is gone: remove its notification. True if there was one."""
        with self._lock:
            self._load()
            found = [i for i in self._items if i.key == key]
            if not found:
                return False
            self._items = [i for i in self._items if i.key != key]
            self._changed()
        self._tell_closed(found, dismissed=False)
        return True

    # ── what the pages do ────────────────────────────────────────────────────

    def snapshot(self, perms: Iterable) -> list[dict]:
        """What a user with these capabilities sees, newest first."""
        with self._lock:
            self._load()
            shown = [i for i in self._items if not i.dismissed and visible_to(i, perms)]
        return [i.public() for i in sorted(shown, key=lambda i: i.updated, reverse=True)]

    def mark_read(self, ids: Iterable[str], perms: Iterable) -> None:
        wanted = set(ids)
        with self._lock:
            self._load()
            hit = [i for i in self._items if i.id in wanted and not i.read and visible_to(i, perms)]
            for item in hit:
                item.read = True
            if hit:
                self._changed()

    def dismiss(self, ids: Iterable[str], perms: Iterable) -> None:
        """Hide notifications from everybody. One with a key stays (hidden) until its producer resolves it, so that the
        same unchanged problem is not raised again; one without a key is simply removed."""
        wanted = set(ids)
        with self._lock:
            self._load()
            hit = self._dismiss([i for i in self._items if i.id in wanted and not i.dismissed and not i.persistent
                                 and visible_to(i, perms)])
        self._tell_closed(hit, dismissed=True)

    def dismiss_all(self, perms: Iterable) -> None:
        with self._lock:
            self._load()
            hit = self._dismiss([i for i in self._items if not i.dismissed and not i.persistent and visible_to(i, perms)])
        self._tell_closed(hit, dismissed=True)

    def _dismiss(self, hit: list[Notification]) -> list[Notification]:
        if not hit:
            return hit
        for item in hit:
            item.dismissed = item.read = True
        self._items = [i for i in self._items if i.key or i not in hit]
        self._changed()
        return hit

    def listen_action(self, callback: Callable[[Notification, str], None]):
        """`callback(notification, action_id)` runs when somebody invokes an action in the web UI (it tells the sender)."""
        self._action_listeners.append(callback)

    def invoke(self, nid: str, action: str, perms: Iterable) -> bool:
        """Somebody clicked the notification (`default`) or one of its action buttons. True if there was such an action to run."""
        with self._lock:
            self._load()
            item = next((i for i in self._items if i.id == nid and not i.dismissed and visible_to(i, perms)), None)
            if item is None or not any(a[0] == action for a in item.actions):
                return False
            resident = item.resident
        for callback in self._action_listeners:
            try:
                callback(item, action)
            except Exception:
                logger.exception('A notification action listener failed')
        if not resident:
            with self._lock:
                hit = self._dismiss([item]) if not item.dismissed else []
            self._tell_closed(hit, dismissed=True)
        return True

    def expire(self, key: str) -> bool:
        """The pop-up time of `key` is over: its sender can no longer be told anything (its actions go). It stays in the list."""
        with self._lock:
            self._load()
            item = next((i for i in self._items if i.key == key), None)
            if item is None or not item.actions:
                return item is not None
            item.actions = []
            self._changed()
        return True

    def listen_closed(self, callback: Callable[[Notification, bool], None]):
        """`callback(notification, dismissed)` runs (in the thread that did it) when a notification leaves the list: dismissed by
        somebody in the web UI (True) or resolved by its producer (False). It must be quick and must not raise."""
        self._closed_listeners.append(callback)

    def _tell_closed(self, items: list[Notification], dismissed: bool):
        for item in items:
            for callback in self._closed_listeners:
                try:
                    callback(item, dismissed)
                except Exception:
                    logger.exception('A notification listener failed')

    # ── pushing changes to the open pages ────────────────────────────────────

    def bind(self, loop: asyncio.AbstractEventLoop, push: Callable[[], Awaitable[None]]):
        """`push` is awaited on `loop` whenever the list changed; changes made at the same moment make one call."""
        self._loop, self._push = loop, push

    def _changed(self):
        self._save()
        if self._loop is None or self._push is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._schedule_push)
        except RuntimeError:
            pass        # the loop is closed: the manager is going down

    def _schedule_push(self):
        if self._push_scheduled:
            return
        self._push_scheduled = True
        self._loop.create_task(self._run_push(), name='notifications_push')

    async def _run_push(self):
        await asyncio.sleep(0)
        self._push_scheduled = False
        try:
            await self._push()
        except Exception:
            logger.exception('Pushing the notifications failed')


notifications = NotificationCenter()
