"""
The feeds a node follows and what they last said.

* `ticker_sources.json` beside the playlists: the configuration (a list of TickerSource), part of the backup.
* `/var/lib/unitotem/ticker-cache.json`: what each feed answered last (items, ETag, Last-Modified), so a restart shows
  a bar at once and a conditional GET still works. It is state, not configuration: NOT in the backup. It has a version
  and every field is optional on reading (a rollback to the previous release must read what this one wrote).
* The merged list: the items of every enabled source in one list, de-duplicated (by link, else by the id the feed gave
  within its source), newest first, capped at 500.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger
from pydantic import ValidationError

from utils.models.ticker import MAX_SOURCES, TickerSource

CACHE_FILE = Path('/var/lib/unitotem/ticker-cache.json')
FILE_VERSION = 1
MERGED_CAP = 500
MAX_ITEMS_PER_SOURCE = 500


def sources_file() -> Path:
    from utils.models.command_line import cmdargs
    return Path(cmdargs.assets_file).parent / 'ticker_sources.json'


@dataclass
class SourceState:
    """What is known about a source at run time (the cache file holds the first four)."""
    items: list = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    last_ok: float = 0.0
    last_try: float = 0.0
    error: str = ''
    fails: int = 0
    next_due: float = 0.0           # epoch seconds; 0 = as soon as possible
    fetching: bool = False


def merge(per_source: list[tuple[TickerSource, list]], cap: int = MERGED_CAP) -> list[dict]:
    """The merged list: each item as `{time, source, title, summary, link}`, newest first."""
    seen: set = set()
    out = []
    for source, items in per_source:
        for item in items:
            key = item.get('link') or f"{source.id}:{item.get('id')}"
            own = f"{source.id}:{item.get('id')}"
            if key in seen or own in seen:
                continue
            seen.add(key)
            seen.add(own)
            out.append({'time': item.get('time') or 0, 'source': source.label, 'title': item['title'],
                        'summary': item.get('summary', ''), 'link': item.get('link', '')})
    out.sort(key=lambda i: -i['time'])          # stable: on a tie the source listed first wins
    return out[:cap]


class TickerStore:
    def __init__(self, sources_path: Optional[Path] = None, cache_path: Path = CACHE_FILE,
                 clock: Callable[[], float] = time.time):
        self._sources_path = sources_path
        self.cache_path = cache_path
        self.clock = clock
        self.sources: list[TickerSource] = []
        self.state: dict[str, SourceState] = {}
        self.load()

    # ── configuration ────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._sources_path or sources_file()

    def load(self):
        self.sources, self.state = [], {}
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            logger.error('Cannot read the news sources from {} ({}), starting with none', self.path, exc)
            data = {}
        self.set_sources(data.get('sources', []) if isinstance(data, dict) else [], save=False)
        self._load_cache()

    def set_sources(self, raw: list, save: bool = True):
        """Replaces every source (restore, load): an invalid entry is skipped, the others kept."""
        sources, ids = [], set()
        for entry in raw[:MAX_SOURCES]:
            try:
                source = TickerSource.model_validate(entry)
            except ValidationError as exc:
                logger.warning('A news source is not valid and is ignored: {}', exc.errors()[0]['msg'])
                continue
            if source.id in ids:
                continue
            ids.add(source.id)
            sources.append(source)
        self.sources = sources
        self.state = {s.id: self.state.get(s.id, SourceState()) for s in sources}
        if save:
            self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'version': FILE_VERSION, 'sources': [s.model_dump() for s in self.sources]},
                                      indent=2))
            tmp.replace(self.path)
        except OSError as exc:
            logger.error('Cannot save the news sources to {}: {}', self.path, exc)

    def get(self, source_id: str) -> TickerSource:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(f'No news source {source_id}')

    def add(self, source: TickerSource) -> TickerSource:
        if len(self.sources) >= MAX_SOURCES:
            raise ValueError(f'At most {MAX_SOURCES} sources are allowed')
        if any(s.url == source.url for s in self.sources):
            raise ValueError('This feed is already in the list')
        self.sources.append(source)
        self.state[source.id] = SourceState()
        self.save()
        return source

    def edit(self, source_id: str, **changes) -> TickerSource:
        """Changes fields of a source (None = unchanged). A new address forgets what the old one said."""
        source = self.get(source_id)
        new = source.model_copy()
        for name, value in changes.items():
            if value is not None:
                setattr(new, name, value)          # validated on assignment
        if new.url != source.url:
            if any(s.url == new.url and s.id != source_id for s in self.sources):
                raise ValueError('This feed is already in the list')
            self.state[source_id] = SourceState()
        self.sources[self.sources.index(source)] = new
        self.save()
        return new

    def delete(self, source_id: str):
        source = self.get(source_id)
        self.sources.remove(source)
        self.state.pop(source_id, None)
        self.save()
        self.save_cache()

    # ── what the feeds said ──────────────────────────────────────────────

    def record_success(self, source_id: str, items: list, etag: Optional[str], last_modified: Optional[str]):
        """A fetch that returned entries: those without a date get the moment they were first seen."""
        st = self.state[source_id]
        now = self.clock()
        known = {i['id']: i['time'] for i in st.items}
        fresh = []
        for item in items[:MAX_ITEMS_PER_SOURCE]:
            item = dict(item)
            if item.get('time') is None:
                item['time'] = known.get(item['id'], now)
            fresh.append(item)
        st.items, st.etag, st.last_modified = fresh, etag, last_modified
        st.last_ok = st.last_try = now
        st.error, st.fails = '', 0

    def record_not_modified(self, source_id: str):
        st = self.state[source_id]
        st.last_ok = st.last_try = self.clock()
        st.error, st.fails = '', 0

    def record_failure(self, source_id: str, error: str):
        st = self.state[source_id]
        st.last_try = self.clock()
        st.error = error
        st.fails += 1

    def merged(self) -> list[dict]:
        return merge([(s, self.state[s.id].items) for s in self.sources if s.enabled and s.id in self.state])

    # ── cache ────────────────────────────────────────────────────────────

    def _load_cache(self):
        try:
            data = json.loads(self.cache_path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logger.warning('Cannot read the news cache {} ({}), ignored', self.cache_path, exc)
            return
        for sid, entry in (data.get('sources', {}) if isinstance(data, dict) else {}).items():
            st = self.state.get(sid)
            if st is None or not isinstance(entry, dict):
                continue
            try:
                st.items = [{'id': str(i['id']), 'time': float(i['time']) if i.get('time') else None,
                             'title': str(i['title']), 'summary': str(i.get('summary', '')),
                             'link': str(i.get('link', ''))} for i in entry.get('items', [])][:MAX_ITEMS_PER_SOURCE]
            except (KeyError, TypeError, ValueError):
                st.items = []
            st.etag, st.last_modified = entry.get('etag'), entry.get('last_modified')
            st.last_ok = float(entry.get('last_ok') or 0)

    def save_cache(self):
        data = {'version': FILE_VERSION, 'sources': {
            sid: {'items': st.items, 'etag': st.etag, 'last_modified': st.last_modified, 'last_ok': st.last_ok}
            for sid, st in self.state.items()}}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            logger.warning('Cannot save the news cache to {}: {}', self.cache_path, exc)

    # ── for the web UI ───────────────────────────────────────────────────

    def public_sources(self) -> list[dict]:
        out = []
        for source in self.sources:
            st = self.state.get(source.id, SourceState())
            out.append({**source.model_dump(), 'label': source.label, 'items': len(st.items), 'last_ok': st.last_ok,
                        'last_try': st.last_try, 'error': st.error, 'fetching': st.fetching})
        return out
