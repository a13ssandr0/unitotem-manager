"""
One HTTP fetch of a feed: conditional GET, timeouts, a cap on the size of the answer (after decompression) and on the
number of redirects, http/https only. It raises `FetchError` with a short message that is safe to show to the user
(never an exception text, which may hold addresses or paths).
"""
from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import aiohttp

from utils.ticker import feed

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
TOTAL_TIMEOUT = 25.0
CONNECT_TIMEOUT = 10.0
USER_AGENT = 'UniTotem-Ticker/1'


class FetchError(Exception):
    """A fetch that failed; `str(error)` is shown in the web UI and in the notification."""


@dataclass
class FetchResult:
    not_modified: bool = False
    items: list = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None


async def fetch(url: str, etag: Optional[str] = None, last_modified: Optional[str] = None, *,
                insecure: bool = False, max_bytes: int = MAX_BYTES, total_timeout: float = TOTAL_TIMEOUT,
                now: Optional[float] = None) -> FetchResult:
    """Downloads and parses a feed. `insecure` accepts any TLS certificate (the `allow_insecure_certs` setting)."""
    if urlsplit(url).scheme.lower() not in ('http', 'https'):
        raise FetchError('Only http and https addresses are fetched')
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/rss+xml, application/atom+xml, application/xml, '
               'text/xml;q=0.9, */*;q=0.5'}
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified
    timeout = aiohttp.ClientTimeout(total=total_timeout, sock_connect=CONNECT_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, auto_decompress=True) as session:
            async with session.get(url, max_redirects=MAX_REDIRECTS, ssl=False if insecure else None) as response:
                if response.status == 304:
                    return FetchResult(not_modified=True, etag=etag, last_modified=last_modified)
                if response.status != 200:
                    raise FetchError(f'The server answered HTTP {response.status}')
                declared = response.content_length
                if declared is not None and declared > max_bytes:
                    raise FetchError('The feed is too large')
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data += chunk
                    if len(data) > max_bytes:
                        raise FetchError('The feed is too large')
                new_etag = response.headers.get('ETag')
                new_modified = response.headers.get('Last-Modified')
    except FetchError:
        raise
    except aiohttp.TooManyRedirects:
        raise FetchError('Too many redirects')
    except (asyncio.TimeoutError, TimeoutError):
        raise FetchError('The server did not answer in time')
    except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError, ssl.SSLError):
        raise FetchError('The TLS certificate of the server is not valid')
    except aiohttp.InvalidURL:
        raise FetchError('The address is not valid')
    except aiohttp.ClientError:
        raise FetchError('Cannot connect to the server')
    loop = asyncio.get_running_loop()
    try:
        # feedparser is CPU bound and synchronous: a worker thread keeps the loop (the whole manager) responsive
        items = await loop.run_in_executor(None, feed.parse, bytes(data), now)
    except feed.FeedError as exc:
        raise FetchError(str(exc))
    except ImportError:
        raise FetchError('The feedparser library is not installed')
    except Exception:       # feedparser is meant not to raise on bad input, a surprise must still not take the loop down
        raise FetchError('The feed cannot be read')
    return FetchResult(items=items, etag=new_etag, last_modified=new_modified)
