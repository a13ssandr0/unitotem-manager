#!/usr/bin/env python3
"""
Throwaway feed server for trying the ticker (never part of the product): feeds with dates relative to NOW, so "today" and
"last two hours" selections have something to select, plus the awkward ones (hostile text, huge, slow, 304, redirect loop).

    python3 tools/ticker/feedserver.py [port]      # default 8765, listens on all interfaces

  /live.xml     RSS 2.0, 12 headlines, newest 2 minutes old, one every 10 minutes back in time (ETag, 304 when unchanged)
  /atom.xml     Atom, 8 entries, one per 25 minutes
  /big.xml      RSS 2.0 with 500 items spread over the last 10 days
  /dated.xml    RSS 2.0 with one item per hour for 3 days (for the count/today/range selections)
  /hostile.xml  titles with markup, Rich tags, braces and control characters
  /dateless.xml items without a date
  /slow.xml     answers after 40 s;  /huge.bin 5 MB;  /loop redirects forever;  /fail answers 500;  /notfeed.html is HTML
"""
import hashlib
import sys
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

TOPICS = ['Maltempo, allerta arancione in tre regioni', 'Talks continue as the deadline approaches', 'Markets close higher on earnings optimism',
          'Il museo riapre dopo il restauro', 'New bridge opens to traffic after two years of work', 'Scoperta una nuova specie di rana in Amazzonia',
          'City council approves the budget for next year', 'Treno deragliato, nessun ferito', 'Researchers report a breakthrough in battery design',
          'Il festival del cinema annuncia il programma', 'Heavy snow expected in the north overnight', 'Elezioni, affluenza in aumento nel pomeriggio']


def rss(items, title='Test feed'):
    body = ''.join(
        f'<item><title>{escape(t)}</title><link>http://example.org/{abs(hash(t)) % 10**8}</link><guid isPermaLink="false">{g}</guid>'
        f'{"<pubDate>" + formatdate(ts, usegmt=True) + "</pubDate>" if ts else ""}<description>{escape(d)}</description></item>'
        for t, g, ts, d in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>'
            f'<link>http://example.org/</link><description>x</description>{body}</channel></rss>').encode()


def live():
    now = time.time()
    return rss([(TOPICS[i % len(TOPICS)], f'live-{i}', now - 120 - i * 600, f'Summary of the story number {i}.') for i in range(12)], 'Live')


def atom():
    now = time.time()
    entries = ''.join(
        f'<entry><title>{escape("Atom: " + TOPICS[(i + 3) % len(TOPICS)])}</title><id>urn:atom:{i}</id><link href="http://example.org/a{i}"/>'
        f'<updated>{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 300 - i * 1500))}</updated><summary>Atom summary {i}</summary></entry>'
        for i in range(8))
    return f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom test</title>{entries}</feed>'.encode()


def big():
    now = time.time()
    return rss([(f'Big feed item {i}: {TOPICS[i % len(TOPICS)]}', f'big-{i}', now - 600 - i * 1700, '') for i in range(500)], 'Big')


def dated():
    now = time.time()
    return rss([(f'{i} hours ago: {TOPICS[i % len(TOPICS)]}', f'dated-{i}', now - i * 3600 - 30, '') for i in range(72)], 'Dated')


def hostile():
    now = time.time()
    titles = ['[bold red]PWNED[/] {title} {{x}} \\[', '<b>bold?</b> <img src=x onerror=alert(1)> <script>alert(2)</script>',
              'Control\x07chars\x1b[31m and\ttabs', '{source!u} {time:HH:mm} [on red]x[/on red]', 'Emoji \U0001F600 and RTL ‮evil']
    return rss([(t, f'h-{i}', now - 300 - i * 60, '<p>Hello <b>world</b></p>') for i, t in enumerate(titles)], 'Hostile')


def dateless():
    return rss([(f'No date headline {i}', f'nd-{i}', None, '') for i in range(5)], 'Dateless')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print(self.client_address[0], self.path, *args, flush=True)

    def reply(self, code, body=b'', ctype='application/rss+xml', headers=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        feeds = {'/live.xml': live, '/atom.xml': atom, '/big.xml': big, '/dated.xml': dated, '/hostile.xml': hostile,
                 '/dateless.xml': dateless}
        if path in feeds:
            body = feeds[path]()
            etag = '"' + hashlib.md5(body).hexdigest() + '"'
            if path == '/live.xml' and self.headers.get('If-None-Match') == etag:
                return self.reply(304, headers={'ETag': etag})
            return self.reply(200, body, headers={'ETag': etag} if path == '/live.xml' else None)
        if path == '/slow.xml':
            time.sleep(40)
            return self.reply(200, live())
        if path == '/huge.bin':
            return self.reply(200, b'<!--' + b'a' * 5_000_000 + b'-->')
        if path == '/loop':
            return self.reply(302, headers={'Location': '/loop'})
        if path == '/fail':
            return self.reply(500)
        if path == '/notfeed.html':
            return self.reply(200, b'<html><body>not a feed</body></html>', 'text/html')
        return self.reply(404)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    server.daemon_threads = True
    print('feeds on port', port, flush=True)
    server.serve_forever()
