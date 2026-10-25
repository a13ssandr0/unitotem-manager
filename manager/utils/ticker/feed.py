"""
From the bytes of a feed to a list of plain items. Everything that comes from a feed is untrusted: it is stripped of
markup, of control characters and capped here, so what the rest of UniTotem (and the page, which also renders it as
text only) handles is bounded plain text.
"""
from __future__ import annotations

import calendar
import html
import io
import re
import time
from typing import Optional
from urllib.parse import urlsplit

MAX_TITLE = 300
MAX_SUMMARY = 400
MAX_LINK = 1000
MAX_ID = 300
MAX_ENTRIES_PER_FEED = 500       # a feed with more is cut at its first entries (the merged list holds 500 in all)

_TAG_RE = re.compile(r'<[^>]*>')
_BLOCK_RE = re.compile(r'(?i)<\s*(?:br|/p|/div|/li|/h[1-6])\s*/?>')
_SPACE_RE = re.compile(r'\s+')


class FeedError(Exception):
    """The bytes are not a usable feed (the message is short and safe to show)."""


def clean_text(value, limit: int) -> str:
    """Plain text of a field that may hold HTML: tags removed, entities decoded, control characters dropped,
    whitespace collapsed, cut at `limit` characters (with an ellipsis)."""
    if not value:
        return ''
    text = _BLOCK_RE.sub(' ', str(value))
    text = _TAG_RE.sub('', text)
    text = html.unescape(text)
    text = ''.join(c if c.isprintable() else ' ' for c in text)
    text = _SPACE_RE.sub(' ', text).strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + '…'
    return text


def clean_link(value) -> str:
    """The link of an entry if it is an http(s) address without control characters, else nothing."""
    value = str(value or '').strip()
    if not value or len(value) > MAX_LINK or any(c.isspace() or ord(c) < 32 for c in value):
        return ''
    try:
        parts = urlsplit(value)
        return value if parts.scheme.lower() in ('http', 'https') and parts.hostname else ''
    except ValueError:
        return ''


def parse(data: bytes, now: Optional[float] = None) -> list[dict]:
    """Entries of an RSS 2.0 / RSS 1.0 (RDF) / Atom document as `{id, time, title, summary, link}`, in the order the
    feed gives them. `time` is the epoch seconds the entry says (clamped to `now`: a feed from the future cannot pin
    its entries on top forever) or None when it says nothing: the caller decides (it knows when it first saw it).
    Raises FeedError when the document is not a feed."""
    import feedparser           # deferred: only the machines that fetch feeds need it, and a missing one is reported

    now = time.time() if now is None else now
    # a file-like object: feedparser would try to open a bytes argument as a file name
    parsed = feedparser.parse(io.BytesIO(data))
    entries = parsed.get('entries') or []
    if not entries:
        # an empty feed is legal, a page that is not a feed is not: tell them apart by the version feedparser found
        if parsed.get('version'):
            return []
        raise FeedError('Not an RSS or Atom feed')
    items = []
    for entry in entries[:MAX_ENTRIES_PER_FEED]:
        title = clean_text(entry.get('title'), MAX_TITLE)
        summary = clean_text(entry.get('summary') or entry.get('description'), MAX_SUMMARY)
        if not title:
            title = summary
            summary = ''
        if not title:
            continue
        link = clean_link(entry.get('link'))
        guid = str(entry.get('id') or link or title)[:MAX_ID]
        stamp = entry.get('published_parsed') or entry.get('updated_parsed')
        when = None
        if stamp:
            try:
                when = float(min(calendar.timegm(stamp), int(now)))
            except (OverflowError, ValueError, TypeError):
                when = None
        items.append({'id': guid, 'time': when, 'title': title, 'summary': summary, 'link': link})
    return items
