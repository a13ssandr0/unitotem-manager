"""The files a client shows come from its server: what is wanted, what is sent, what the client keeps and what it drops."""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_remote_transfer import Pair, run                       # noqa: E402  (two channels wired to each other)

from utils.remote import bundle, filecache, transfer            # noqa: E402


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def pair(tmp_path):
    """`src` is the server's uploads, `dst` the client's cache folder."""
    pair = Pair(tmp_path, apply=lambda manifest, staged: _apply(pair, manifest, staged))
    return pair


async def _apply(pair, manifest, staged):
    filecache.apply_cache(manifest, staged, target=pair.dst)


def test_a_client_is_brought_up_to_date_and_only_what_differs_is_sent(pair):
    for name, content in (('a.jpg', b'same'), ('b.mp4', b'new content'), ('c.pdf', b'brand new')):
        (pair.src / name).write_bytes(content)
    (pair.dst / 'a.jpg').write_bytes(b'same')                  # already there, identical
    (pair.dst / 'b.mp4').write_bytes(b'old content')           # there, but another version
    (pair.dst / 'z.png').write_bytes(b'nobody wants it')      # not wanted any more
    manifest = run(filecache.build_manifest(filecache.inventory(pair.dst), {'a.jpg', 'b.mp4', 'c.pdf'}, pair.src))
    assert [f['name'] for f in manifest['files']] == ['b.mp4', 'c.pdf'] and manifest['keep'] == ['a.jpg', 'b.mp4', 'c.pdf']
    run(transfer.send_bundle(pair.a, manifest, pair.src))
    assert sorted(p.name for p in pair.dst.iterdir()) == ['a.jpg', 'b.mp4', 'c.pdf']
    assert (pair.dst / 'b.mp4').read_bytes() == b'new content' and (pair.dst / 'c.pdf').read_bytes() == b'brand new'
    assert len(pair.sent_files) == 2                           # a.jpg was not sent again
    assert run(filecache.build_manifest(filecache.inventory(pair.dst), {'a.jpg', 'b.mp4', 'c.pdf'}, pair.src)) is None


def test_a_file_that_is_no_longer_wanted_is_removed_even_when_nothing_is_sent(pair):
    (pair.src / 'a.jpg').write_bytes(b'a')
    (pair.dst / 'a.jpg').write_bytes(b'a')
    (pair.dst / 'old.mp4').write_bytes(b'x')
    manifest = run(filecache.build_manifest(filecache.inventory(pair.dst), {'a.jpg'}, pair.src))
    assert manifest['files'] == [] and manifest['keep'] == ['a.jpg']
    run(transfer.send_bundle(pair.a, manifest, pair.src))
    assert [p.name for p in pair.dst.iterdir()] == ['a.jpg']


def test_a_wanted_file_the_server_does_not_have_is_left_out(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'a')
    manifest = run(filecache.build_manifest({}, {'a.jpg', 'gone.jpg'}, tmp_path))
    assert [f['name'] for f in manifest['files']] == ['a.jpg'] and manifest['keep'] == ['a.jpg']


def test_each_transfer_has_an_id_of_its_own(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'a')
    one, two = (run(filecache.build_manifest({}, {'a.jpg'}, tmp_path)) for _ in range(2))
    assert one['id'] != two['id'] and transfer.validate_manifest(one) and one['kind'] == 'cache'


def test_a_client_that_lost_its_cache_gets_the_files_again(pair):
    (pair.src / 'a.jpg').write_bytes(b'a')
    first = run(filecache.build_manifest({}, {'a.jpg'}, pair.src))
    run(transfer.send_bundle(pair.a, first, pair.src))
    (pair.dst / 'a.jpg').unlink()
    second = run(filecache.build_manifest(filecache.inventory(pair.dst), {'a.jpg'}, pair.src))
    run(transfer.send_bundle(pair.a, second, pair.src))
    assert (pair.dst / 'a.jpg').read_bytes() == b'a'


def test_the_inventory_lists_real_files_with_their_digests_only(tmp_path):
    (tmp_path / 'a.jpg').write_bytes(b'a')
    (tmp_path / 'b.mp4.part').write_bytes(b'half')
    (tmp_path / '.hidden').write_bytes(b'x')
    (tmp_path / 'folder').mkdir()
    assert filecache.inventory(tmp_path) == {'a.jpg': digest(b'a')}
    assert filecache.inventory(tmp_path / 'missing') == {}


def test_what_a_client_says_it_has_is_checked():
    good = {'a.jpg': digest(b'a')}
    assert filecache.clean_have(good) == good
    assert filecache.clean_have({'../x': digest(b'a'), 'b.jpg': 'nothex', 'c.jpg': 5, '.d': digest(b'd')}) == {}
    assert filecache.clean_have(['a']) == {} and filecache.clean_have(None) == {}
    assert filecache.clean_have({f'f{n}.jpg': digest(b'a') for n in range(filecache.MAX_NAMES + 1)}) == {}


def test_the_files_a_client_needs_are_those_of_the_playlists_its_windows_show(monkeypatch):
    monkeypatch.setattr(bundle, 'playlist_files', lambda: {'main': {'a.jpg'}, 'pane': {'b.mp4'}, 'other': {'c.pdf'}, 'none': set()})
    monkeypatch.setattr(bundle, 'assignments', lambda: [('peer', 'HDMI-A-1', 'main'), ('server-side', 'DP-1', 'other')])
    monkeypatch.setattr(bundle, 'pane_playlists', lambda viewer: ['pane'] if viewer == 'peer' else [])
    assert filecache.wanted_names('peer') == {'a.jpg', 'b.mp4'}
    assert filecache.wanted_names('nobody') == set()


def test_clearing_the_cache_removes_it(tmp_path):
    cache = tmp_path / 'remote-files'
    cache.mkdir()
    (cache / 'a.jpg').write_bytes(b'a')
    filecache.clear(cache)
    assert not cache.exists()
    filecache.clear(cache)                                     # again: nothing to remove, no error


def test_the_manifest_of_a_cache_transfer_is_bounded():
    base = {'id': 'a' * 32, 'playlists': [], 'files': [], 'assignments': [], 'kind': 'cache'}
    assert transfer.validate_manifest({**base, 'keep': ['a.jpg']})
    for bad in ({'keep': 'a.jpg'}, {'keep': ['../x']}, {'keep': [f'f{n}' for n in range(transfer.MAX_FILES + 1)]}, {'kind': 'unknown'}):
        with pytest.raises(transfer.TransferError):
            transfer.validate_manifest({**base, **bad})


# ── the service: what it says and does ───────────────────────────────────────

from test_remote_pairing import node                              # noqa: E402,F401  (a RemoteService with its own state)


def test_a_client_that_says_what_it_has_makes_the_server_look_at_its_files(node, monkeypatch):
    import asyncio
    peer = type('Peer', (), {'id': 'client-1', 'name': 'lobby', 'enabled': True})()
    looked = []
    monkeypatch.setattr(node, 'cache_poke', lambda delay=1.5: looked.append(delay))
    run(node._server_other(peer, {'link': 'cache-have', 'files': {'a.jpg': digest(b'a'), '../evil': digest(b'x')}}))
    assert node._have['client-1'] == {'a.jpg': digest(b'a')} and looked == [0.2]


def test_the_server_sends_a_client_what_its_windows_need_and_shows_it_again(node, monkeypatch, tmp_path):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'a.jpg').write_bytes(b'a')
    service_module = __import__('utils.remote.service', fromlist=['upload_manager', 'send_bundle'])
    monkeypatch.setattr(service_module, 'upload_manager', type('U', (), {'folder': uploads})())
    monkeypatch.setattr(filecache, 'upload_manager', type('U', (), {'folder': uploads})())
    monkeypatch.setattr(filecache, 'wanted_names', lambda viewer: {'a.jpg'})
    sent, shown = [], []

    async def fake_send(channel, manifest, folder, progress=None):
        sent.append(manifest)

    monkeypatch.setattr(service_module, 'send_bundle', fake_send)
    monkeypatch.setattr(node, '_channel_for', lambda *a, **k: object())
    from webview import controller
    monkeypatch.setattr(controller.WebviewManager, 'get_instance', classmethod(lambda cls: type('M', (), {'refresh_viewer': lambda self, v: shown.append(v)})()))
    peer = type('Peer', (), {'id': 'client-1', 'name': 'lobby', 'enabled': True})()
    sock = type('Sock', (), {'ws': type('W', (), {'send': None})()})()
    node._have['client-1'] = {}
    run(node._sync_cache(peer, sock))
    assert [f['name'] for f in sent[0]['files']] == ['a.jpg'] and sent[0]['kind'] == 'cache'
    assert node._have['client-1'] == {'a.jpg': digest(b'a')} and shown == ['client-1']
    run(node._sync_cache(peer, sock))                              # up to date now: nothing more is sent
    assert len(sent) == 1
