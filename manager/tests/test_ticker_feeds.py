"""Feeds: parsing (RSS 2.0, Atom, RDF, malformed, hostile), the fetcher against local servers (304, redirect loop,
huge, slow, wrong status, not a feed, redirect to a non-http scheme), merge and de-duplication, the store and its cache."""
import asyncio
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from utils.models.ticker import TickerSource
from utils.ticker import feed, fetcher
from utils.ticker.store import MERGED_CAP, TickerStore, merge

RSS = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>T</title>
<item><title>First &amp; best</title><link>http://ex.org/1</link><guid>g1</guid>
 <pubDate>Mon, 05 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title><![CDATA[<script>alert(1)</script>Second]]></title><link>javascript:alert(1)</link>
 <pubDate>Mon, 05 Oct 2026 09:00:00 +0200</pubDate></item>
<item><title>No date</title><link>http://ex.org/3</link></item>
</channel></rss>'''

ATOM = b'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title type="html">Atom &lt;i&gt;one&lt;/i&gt;</title><id>urn:1</id><link href="https://ex.org/a1"/>
 <updated>2026-10-05T08:30:00Z</updated><summary>Sum</summary></entry>
<entry><title>Atom two</title><id>urn:2</id><link href="https://ex.org/a2"/><published>2026-10-04T08:30:00+02:00</published></entry>
</feed>'''

RDF = b'''<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="http://ex.org/"><title>R</title></channel>
<item rdf:about="http://ex.org/r1"><title>Rdf item</title><link>http://ex.org/r1</link><dc:date>2026-10-05T07:00:00Z</dc:date></item>
</rdf:RDF>'''

NOW = 1791194400.0     # 2026-10-05 10:00:00 UTC


# ── parsing ──────────────────────────────────────────────────────────────────

def test_rss_items_are_plain_text():
    items = feed.parse(RSS, now=NOW)
    assert [i['title'] for i in items][0] == 'First & best'
    assert items[0]['summary'] == 'Hello world'
    assert items[0]['time'] == 1791194400.0
    assert items[0]['id'] == 'g1' and items[0]['link'] == 'http://ex.org/1'
    # markup of a title is removed, a javascript: link is dropped, +0200 is converted
    assert items[1]['title'] == 'alert(1)Second' or items[1]['title'].endswith('Second')
    assert '<' not in items[1]['title'] and items[1]['link'] == ''
    assert items[1]['time'] == 1791194400.0 - 3600 * 3
    assert items[2]['time'] is None      # the store decides: it knows when it first saw it


def test_atom_and_rdf():
    atom = feed.parse(ATOM, now=NOW)
    assert [i['title'] for i in atom] == ['Atom one', 'Atom two']
    assert atom[0]['time'] == 1791194400.0 - 5400 and atom[0]['link'] == 'https://ex.org/a1'
    assert atom[1]['time'] == NOW - 99000
    rdf = feed.parse(RDF, now=NOW)
    assert rdf[0]['title'] == 'Rdf item' and rdf[0]['time'] == 1791194400.0 - 10800


def test_future_dates_are_clamped_to_now():
    data = RSS.replace(b'Mon, 05 Oct 2026 10:00:00 GMT', b'Mon, 05 Oct 2031 10:00:00 GMT')
    assert feed.parse(data, now=NOW)[0]['time'] == NOW


def test_malformed_but_recoverable_feed():
    data = RSS.replace(b'</channel></rss>', b'')       # truncated
    assert feed.parse(data, now=NOW)[0]['title'] == 'First & best'


@pytest.mark.parametrize('data', [b'', b'<html><body>hello</body></html>', b'\x00\x01\x02', b'not xml at all'])
def test_not_a_feed_is_an_error(data):
    with pytest.raises(feed.FeedError):
        feed.parse(data)


def test_empty_feed_is_legal():
    assert feed.parse(b'<rss version="2.0"><channel><title>x</title></channel></rss>') == []


def test_bytes_are_never_taken_for_a_file_name(tmp_path):
    """feedparser would open a bytes argument as a path: our wrapper must make that impossible."""
    target = tmp_path / 'secret.xml'
    target.write_text(RSS.decode())
    with pytest.raises(feed.FeedError):
        feed.parse(str(target).encode())


def test_clean_text_and_limits():
    assert feed.clean_text('a<br>b<p>c</p>&nbsp;&lt;x&gt;', 100) == 'a bc <x>'
    assert feed.clean_text('x\x00y\x1b[31mz', 100) == 'x y [31mz'
    out = feed.clean_text('word ' * 1000, 50)
    assert len(out) == 50 and out.endswith('…')
    assert feed.clean_link('http://a.b/c d') == '' and feed.clean_link('ftp://a.b/') == '' and feed.clean_link(None) == ''


def test_many_entries_are_cut():
    body = ''.join(f'<item><title>t{i}</title><link>http://e/{i}</link></item>' for i in range(500))
    items = feed.parse(f'<rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode())
    assert len(items) == feed.MAX_ENTRIES_PER_FEED


# ── merge / dedup ────────────────────────────────────────────────────────────

def src(name, n=1):
    return TickerSource(url=f'http://{name}.example/feed{n}', name=name)


def item(i, t, link='', title=None):
    return {'id': i, 'time': t, 'title': title or f'title {i}', 'summary': '', 'link': link}


def test_merge_sorts_newest_first_and_labels_the_source():
    a, b = src('A'), src('B')
    out = merge([(a, [item('1', 100), item('2', 300)]), (b, [item('1', 200)])])
    assert [(i['source'], i['time']) for i in out] == [('A', 300), ('B', 200), ('A', 100)]


def test_merge_dedups_by_link_across_sources_and_by_id_within_one():
    a, b = src('A'), src('B')
    out = merge([(a, [item('1', 100, 'http://x/1'), item('1', 100, 'http://x/other')]),
                 (b, [item('9', 150, 'http://x/1'), item('9', 160, 'http://x/9')])])
    assert sorted(i['link'] for i in out) == ['http://x/1', 'http://x/9']


def test_merge_is_capped():
    a = src('A')
    assert len(merge([(a, [item(str(i), i, f'http://x/{i}') for i in range(1200)])])) == MERGED_CAP
    assert merge([(a, [item(str(i), i, f'http://x/{i}') for i in range(1200)])])[0]['time'] == 1199


def test_item_without_a_time_sorts_last():
    a = src('A')
    assert merge([(a, [{'id': '1', 'time': None, 'title': 't', 'summary': '', 'link': ''}, item('2', 5)])])[-1]['time'] == 0


# ── store ────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    clock = [1000.0]
    s = TickerStore(tmp_path / 'ticker_sources.json', tmp_path / 'cache.json', clock=lambda: clock[0])
    s.clock_value = clock
    return s


def test_store_persists_sources(store, tmp_path):
    a = store.add(TickerSource(url='http://a.example/rss', name='A', interval=120))
    store.add(TickerSource(url='http://b.example/rss'))
    again = TickerStore(tmp_path / 'ticker_sources.json', tmp_path / 'cache.json')
    assert [s.url for s in again.sources] == ['http://a.example/rss', 'http://b.example/rss']
    assert again.get(a.id).interval == 120


def test_store_refuses_duplicates_and_too_many(store):
    store.add(TickerSource(url='http://a.example/rss'))
    with pytest.raises(ValueError):
        store.add(TickerSource(url='http://a.example/rss'))
    from utils.models.ticker import MAX_SOURCES
    for i in range(MAX_SOURCES - 1):
        store.add(TickerSource(url=f'http://s{i}.example/rss'))
    with pytest.raises(ValueError):
        store.add(TickerSource(url='http://one-too-many.example/rss'))


def test_store_edit_and_new_address_forgets_the_old_state(store):
    a = store.add(TickerSource(url='http://a.example/rss'))
    store.record_success(a.id, [item('1', 50)], 'etag1', None)
    store.edit(a.id, name='Renamed')
    assert store.state[a.id].etag == 'etag1'
    store.edit(a.id, url='http://other.example/rss', interval=10)
    assert store.state[a.id].etag is None and store.state[a.id].items == []
    assert store.get(a.id).interval == 60
    with pytest.raises(KeyError):
        store.edit('nope')


def test_dateless_items_keep_the_time_they_were_first_seen(store):
    a = store.add(TickerSource(url='http://a.example/rss'))
    undated = {'id': 'x', 'time': None, 'title': 't', 'summary': '', 'link': ''}
    store.record_success(a.id, [undated], None, None)
    store.clock_value[0] = 5000
    store.record_success(a.id, [undated], None, None)
    assert store.state[a.id].items[0]['time'] == 1000.0


def test_cache_survives_a_restart_but_is_not_the_configuration(store, tmp_path):
    a = store.add(TickerSource(url='http://a.example/rss'))
    store.record_success(a.id, [item('1', 50, 'http://x/1', 'Hello')], 'E', 'LM')
    store.save_cache()
    again = TickerStore(tmp_path / 'ticker_sources.json', tmp_path / 'cache.json')
    st = again.state[a.id]
    assert st.etag == 'E' and st.last_modified == 'LM' and again.merged()[0]['title'] == 'Hello'
    assert 'Hello' not in (tmp_path / 'ticker_sources.json').read_text()


def test_corrupt_files_do_not_stop_the_store(tmp_path):
    (tmp_path / 'ticker_sources.json').write_text('{ not json')
    (tmp_path / 'cache.json').write_text('[1,2')
    assert TickerStore(tmp_path / 'ticker_sources.json', tmp_path / 'cache.json').sources == []
    (tmp_path / 'ticker_sources.json').write_text(json.dumps({'sources': [
        {'url': 'ftp://bad'}, {'url': 'http://good.example/x'}, 'garbage', {'url': 'http://good.example/x2', 'extra': 1}]}))
    assert [s.url for s in TickerStore(tmp_path / 'ticker_sources.json', tmp_path / 'cache.json').sources] == [
        'http://good.example/x', 'http://good.example/x2']


def test_disabled_sources_are_not_in_the_merged_list(store):
    a = store.add(TickerSource(url='http://a.example/rss'))
    store.record_success(a.id, [item('1', 50)], None, None)
    assert len(store.merged()) == 1
    store.edit(a.id, enabled=False)
    assert store.merged() == []


# ── fetcher against throwaway servers ────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.seen.append((self.path, dict(self.headers)))
        route = Handler.routes.get(self.path.split('?')[0])
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        route(self)


def ok(body, ctype='application/rss+xml', headers=None, gz=False):
    def handle(h):
        data = gzip.compress(body) if gz else body
        h.send_response(200)
        h.send_header('Content-Type', ctype)
        h.send_header('Content-Length', str(len(data)))
        if gz:
            h.send_header('Content-Encoding', 'gzip')
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(data)
    return handle


def redirect(to, code=302):
    def handle(h):
        h.send_response(code)
        h.send_header('Location', to)
        h.send_header('Content-Length', '0')
        h.end_headers()
    return handle


@pytest.fixture
def server():
    Handler.routes, Handler.seen = {}, []
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}'
    yield base
    httpd.shutdown()
    httpd.server_close()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_fetch_rss_and_gzip(server):
    Handler.routes['/rss'] = ok(RSS, headers={'ETag': '"abc"', 'Last-Modified': 'Mon, 05 Oct 2026 10:00:00 GMT'})
    Handler.routes['/gz'] = ok(ATOM, ctype='application/atom+xml', gz=True)
    r = run(fetcher.fetch(server + '/rss', now=NOW))
    assert len(r.items) == 3 and r.etag == '"abc"' and r.last_modified.startswith('Mon, 05 Oct')
    assert len(run(fetcher.fetch(server + '/gz', now=NOW)).items) == 2


def test_conditional_get_sends_validators_and_understands_304(server):
    def handle(h):
        if h.headers.get('If-None-Match') == '"abc"':
            h.send_response(304)
            h.end_headers()
        else:
            ok(RSS, headers={'ETag': '"abc"'})(h)
    Handler.routes['/rss'] = handle
    first = run(fetcher.fetch(server + '/rss'))
    second = run(fetcher.fetch(server + '/rss', etag=first.etag, last_modified='Mon, 05 Oct 2026 10:00:00 GMT'))
    assert second.not_modified and second.items == []
    sent = Handler.seen[-1][1]
    assert sent['If-None-Match'] == '"abc"' and sent['If-Modified-Since'].startswith('Mon, 05')
    assert 'UniTotem' in sent['User-Agent']


def test_redirects_are_followed_but_a_loop_is_cut(server):
    Handler.routes['/old'] = redirect('/rss')
    Handler.routes['/rss'] = ok(RSS)
    Handler.routes['/loop1'] = redirect('/loop2')
    Handler.routes['/loop2'] = redirect('/loop1')
    assert len(run(fetcher.fetch(server + '/old')).items) == 3
    with pytest.raises(fetcher.FetchError, match='redirects'):
        run(fetcher.fetch(server + '/loop1'))
    assert len(Handler.seen) < 20


def test_redirect_to_another_scheme_is_refused(server):
    Handler.routes['/ftp'] = redirect('ftp://127.0.0.1/x')
    Handler.routes['/file'] = redirect('file:///etc/passwd')
    for path in ('/ftp', '/file'):
        with pytest.raises(fetcher.FetchError):
            run(fetcher.fetch(server + path))


def test_huge_answer_is_refused_with_and_without_content_length(server):
    big = b'<rss version="2.0"><channel>' + b'<item><title>x</title></item>' * 100 + b'</channel></rss>'
    Handler.routes['/big'] = ok(big)

    def chunked(h):
        h.send_response(200)
        h.send_header('Content-Type', 'application/rss+xml')
        h.end_headers()          # no Content-Length: HTTP/1.0 style, closed at the end
        for _ in range(100):
            try:
                h.wfile.write(b'<!-- ' + b'a' * 1000 + b' -->')
            except OSError:
                return
    Handler.routes['/stream'] = chunked
    with pytest.raises(fetcher.FetchError, match='too large'):
        run(fetcher.fetch(server + '/big', max_bytes=1000))
    with pytest.raises(fetcher.FetchError, match='too large'):
        run(fetcher.fetch(server + '/stream', max_bytes=5000))


def test_gzip_bomb_is_cut_after_decompression(server):
    bomb = b'<rss version="2.0"><channel>' + b'<!--' + b'a' * 3_000_000 + b'-->' + b'</channel></rss>'
    Handler.routes['/bomb'] = ok(bomb, gz=True)
    with pytest.raises(fetcher.FetchError, match='too large'):
        run(fetcher.fetch(server + '/bomb', max_bytes=100_000))


def test_slow_server_times_out(server):
    def slow(h):
        time.sleep(3)
        try:
            ok(RSS)(h)
        except OSError:
            pass
    Handler.routes['/slow'] = slow
    started = time.monotonic()
    with pytest.raises(fetcher.FetchError, match='in time'):
        run(fetcher.fetch(server + '/slow', total_timeout=0.5))
    assert time.monotonic() - started < 2.5


def test_slow_drip_body_times_out(server):
    def drip(h):
        h.send_response(200)
        h.send_header('Content-Length', '100000')
        h.end_headers()
        try:
            for _ in range(100):
                h.wfile.write(b'x')
                h.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass
    Handler.routes['/drip'] = drip
    with pytest.raises(fetcher.FetchError, match='in time'):
        run(fetcher.fetch(server + '/drip', total_timeout=0.7))


def test_error_statuses_and_not_a_feed(server):
    Handler.routes['/html'] = ok(b'<html><body>hi</body></html>', ctype='text/html')
    Handler.routes['/500'] = lambda h: (h.send_response(500), h.end_headers())
    with pytest.raises(fetcher.FetchError, match='HTTP 404'):
        run(fetcher.fetch(server + '/missing'))
    with pytest.raises(fetcher.FetchError, match='HTTP 500'):
        run(fetcher.fetch(server + '/500'))
    with pytest.raises(fetcher.FetchError, match='Not an RSS'):
        run(fetcher.fetch(server + '/html'))


def test_unreachable_host_and_bad_scheme():
    with pytest.raises(fetcher.FetchError, match='connect'):
        run(fetcher.fetch('http://127.0.0.1:1/x'))
    with pytest.raises(fetcher.FetchError, match='Only http'):
        run(fetcher.fetch('file:///etc/passwd'))
    with pytest.raises(fetcher.FetchError, match='Only http'):
        run(fetcher.fetch('ftp://example.org/x'))


def test_error_messages_do_not_leak_internals(server):
    with pytest.raises(fetcher.FetchError) as exc:
        run(fetcher.fetch('http://127.0.0.1:1/secret-path'))
    assert 'secret-path' not in str(exc.value) and '127.0.0.1' not in str(exc.value)


def test_tls_certificate_is_verified_by_default():
    """A server with a certificate nobody trusts: refused unless `insecure` (allow_insecure_certs) is on."""
    import datetime
    import ssl
    import tempfile
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime(2020, 1, 1))
            .not_valid_after(datetime.datetime(2040, 1, 1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), False).sign(key, hashes.SHA256()))
    with tempfile.TemporaryDirectory() as d:
        open(f'{d}/c.pem', 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
        open(f'{d}/k.pem', 'wb').write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
        Handler.routes, Handler.seen = {'/rss': ok(RSS)}, []
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(f'{d}/c.pem', f'{d}/k.pem')
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f'https://localhost:{httpd.server_address[1]}/rss'
        try:
            with pytest.raises(fetcher.FetchError, match='certificate'):
                run(fetcher.fetch(url))
            assert len(run(fetcher.fetch(url, insecure=True)).items) == 3
        finally:
            httpd.shutdown()
            httpd.server_close()
