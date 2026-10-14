"""The transfer of playlists and uploads: which of them go and which are removed (reference counting), and the protocol that
moves them (integrity, failure, retry), with two channels wired to each other."""
import asyncio
import hashlib
import os

import pytest

from utils.remote import transfer
from utils.remote.transfer import Channel, Plan, Receiver, TransferError, plan_leave, receive_bundle, send_bundle

# ── the rules ────────────────────────────────────────────────────────────────

#   lobby  (a.mp4, logo.png)  shown by the client "c1" only
#   shop   (logo.png, b.mp4)  shown by the client "c1" AND by the server itself ("local")
#   promo  (c.mp4)            shown by "c1" only, but it is the server's default playlist
#   spare  (d.mp4, a.mp4)     shown by nobody
#   other  (e.mp4)            shown by another client "c2"
FILES = {
    'lobby': {'a.mp4', 'logo.png'}, 'shop': {'logo.png', 'b.mp4'}, 'promo': {'c.mp4'},
    'spare': {'d.mp4', 'a.mp4'}, 'other': {'e.mp4'},
}
ASSIGN = [('c1', 'HDMI-1', 'lobby'), ('c1', 'HDMI-2', 'shop'), ('local', 'DP-1', 'shop'), ('c1', 'DP-1', 'promo'),
          ('c2', 'HDMI-1', 'other')]


def test_what_only_the_leaving_node_used_is_removed_and_what_anything_else_uses_stays():
    plan = plan_leave(FILES, ASSIGN, 'c1', default='promo')
    assert set(plan.playlists) == {'lobby', 'shop', 'promo'}                # everything its windows showed goes with it
    assert plan.remove_playlists == {'lobby'}              # shop is also shown by the server, promo is the default
    # a.mp4: used by lobby (removed) and by spare (stays, nobody shows it but it is still a playlist of this node)
    # logo.png: used by shop, which stays;  b.mp4: shop;  c.mp4: promo (stays)
    assert plan.remove_files == set()
    assert plan.files == {'a.mp4', 'logo.png', 'b.mp4', 'c.mp4'}
    assert sorted(plan.assignments) == [('DP-1', 'promo'), ('HDMI-1', 'lobby'), ('HDMI-2', 'shop')]


def test_a_playlist_and_files_used_only_by_the_leaving_node_are_removed():
    files = {'mine': {'x.mp4', 'y.png'}, 'kept': {'z.mp4'}}
    plan = plan_leave(files, [('c1', 'HDMI-1', 'mine'), ('local', 'DP-1', 'kept')], 'c1', default='kept')
    assert plan.playlists == ['mine'] and plan.remove_playlists == {'mine'}
    assert plan.remove_files == {'x.mp4', 'y.png'}


def test_a_file_shared_with_a_playlist_that_stays_stays():
    files = {'mine': {'x.mp4', 'shared.png'}, 'kept': {'shared.png'}}
    plan = plan_leave(files, [('c1', 'HDMI-1', 'mine'), ('local', 'DP-1', 'kept')], 'c1', default='kept')
    assert plan.remove_playlists == {'mine'} and plan.remove_files == {'x.mp4'} and plan.files == {'x.mp4', 'shared.png'}


def test_a_playlist_shown_by_the_leaving_node_on_two_screens_is_sent_once():
    plan = plan_leave({'p': {'a'}}, [('c1', 'A', 'p'), ('c1', 'B', 'p')], 'c1', default=None)
    assert plan.playlists == ['p'] and plan.assignments == [('A', 'p'), ('B', 'p')] and plan.remove_playlists == {'p'}


def test_a_node_that_shows_nothing_takes_nothing_and_nothing_is_removed():
    plan = plan_leave(FILES, ASSIGN, 'nobody', default='promo')
    assert plan == Plan([], set(), [], set(), set())


def test_an_assignment_to_a_playlist_that_no_longer_exists_is_ignored():
    plan = plan_leave({'p': set()}, [('c1', 'A', 'gone'), ('c1', 'B', 'p')], 'c1', default=None)
    assert plan.playlists == ['p']


# ── the manifest ─────────────────────────────────────────────────────────────

def good_manifest(**over):
    m = {'id': 'ab' * 16, 'playlists': [{'playlist_id': 'p', 'assets': []}],
         'files': [{'name': 'a.mp4', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}],
         'assignments': [['HDMI-1', 'p']]}
    m.update(over)
    return m


def test_a_good_manifest_passes():
    assert transfer.validate_manifest(good_manifest())['id'] == 'ab' * 16


@pytest.mark.parametrize('bad', [
    {'id': 'short'}, {'id': 5}, {'playlists': 'x'}, {'files': None}, {'assignments': [['only-one']]},
    {'playlists': [{'playlist_id': 'p'}]},
    {'files': [{'name': '../etc/passwd', 'size': 1, 'sha256': 'a' * 64}]},
    {'files': [{'name': 'sub/a.mp4', 'size': 1, 'sha256': 'a' * 64}]},
    {'files': [{'name': '.hidden', 'size': 1, 'sha256': 'a' * 64}]},
    {'files': [{'name': '', 'size': 1, 'sha256': 'a' * 64}]},
    {'files': [{'name': 'a', 'size': -1, 'sha256': 'a' * 64}]},
    {'files': [{'name': 'a', 'size': 1 << 50, 'sha256': 'a' * 64}]},
    {'files': [{'name': 'a', 'size': 1, 'sha256': 'XYZ'}]},
    {'files': [{'name': 'a', 'size': True and 'x', 'sha256': 'a' * 64}]},
    {'files': [{'name': 'a', 'size': 1, 'sha256': 'a' * 64}, {'name': 'a', 'size': 1, 'sha256': 'a' * 64}]},
    {'files': [{'name': 'a', 'size': 1, 'sha256': 'a' * 64}] * (transfer.MAX_FILES + 1)},
])
def test_hostile_manifests_are_refused(bad):
    with pytest.raises(TransferError):
        transfer.validate_manifest(good_manifest(**bad))


def test_not_even_a_dict():
    for junk in (None, [], 'x', 5):
        with pytest.raises(TransferError):
            transfer.validate_manifest(junk)


def test_the_id_depends_on_the_content_only():
    p, f = [{'playlist_id': 'p'}], [{'name': 'a', 'sha256': 'x'}]
    assert transfer.make_id(p, f, 'adopt') == transfer.make_id(list(p), list(f), 'adopt')
    assert transfer.make_id(p, f, 'adopt') != transfer.make_id(p, [{'name': 'a', 'sha256': 'y'}], 'adopt')
    assert transfer.make_id(p, f, 'adopt') != transfer.make_id(p, f, 'leave')


# ── the protocol ─────────────────────────────────────────────────────────────

class Pair:
    """A sender and a receiver whose channels are wired to each other, like the two ends of a link"""

    def __init__(self, tmp_path, apply=None, free=1 << 40, corrupt=None):
        self.src, self.dst, self.stage = tmp_path / 'src', tmp_path / 'dst', tmp_path / 'stage'
        for d in (self.src, self.dst, self.stage):
            d.mkdir()
        self.applied = []
        self.corrupt = corrupt
        self.sent_files = []

        async def default_apply(manifest, staged):
            self.applied.append((manifest['id'], sorted(p.name for p in staged.iterdir())))

        self.committed = set()
        self.receiver = Receiver(self.stage, self.dst, apply or default_apply, self.committed, free_space=lambda: free)
        self.a, self.b = Channel(self._to_b), Channel(self._to_a)
        self.b.on_begin = lambda ch, first: receive_bundle(ch, first, self.receiver)

    async def _to_b(self, data):
        if isinstance(data, bytes):
            if self.corrupt:
                data = self.corrupt(data)
        elif '"file-begin"' in data:
            self.sent_files.append(data)
        await self.b.dispatch(data)

    async def _to_a(self, data):
        await self.a.dispatch(data)

    def add(self, name, content: bytes):
        (self.src / name).write_bytes(content)
        return {'name': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}

    def manifest(self, files, tid='cd' * 16):
        return {'id': tid, 'playlists': [{'playlist_id': 'p', 'assets': []}], 'files': files, 'assignments': []}


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


def test_files_arrive_intact_and_are_applied_once(tmp_path):
    pair = Pair(tmp_path)
    big = os.urandom(transfer.CHUNK * 3 + 17)             # several chunks
    files = [pair.add('big.mp4', big), pair.add('small.png', b'abc'), pair.add('empty.txt', b'')]
    run(send_bundle(pair.a, pair.manifest(files), pair.src))
    assert pair.applied == [('cd' * 16, ['big.mp4', 'empty.txt', 'small.png'])]
    assert not (pair.stage / ('cd' * 16)).exists()          # staging cleaned up
    assert 'cd' * 16 in pair.committed


def test_a_file_the_receiver_already_has_is_not_sent_again(tmp_path):
    pair = Pair(tmp_path)
    files = [pair.add('same.mp4', b'content'), pair.add('new.mp4', b'fresh')]
    (pair.dst / 'same.mp4').write_bytes(b'content')
    run(send_bundle(pair.a, pair.manifest(files), pair.src))
    assert [('same.mp4' in s) for s in pair.sent_files] == [False] and 'new.mp4' in pair.sent_files[0]


def test_a_file_with_the_same_name_but_other_content_is_sent(tmp_path):
    pair = Pair(tmp_path)
    files = [pair.add('x.mp4', b'mine')]
    (pair.dst / 'x.mp4').write_bytes(b'theirs')
    run(send_bundle(pair.a, pair.manifest(files), pair.src))
    assert len(pair.sent_files) == 1


def test_a_damaged_transfer_is_refused_and_nothing_is_applied(tmp_path):
    pair = Pair(tmp_path, corrupt=lambda data: data[:-1] + bytes([data[-1] ^ 1]))
    files = [pair.add('a.mp4', b'0123456789')]
    with pytest.raises(TransferError, match='damaged'):
        run(send_bundle(pair.a, pair.manifest(files), pair.src))
    assert pair.applied == [] and not (pair.stage / ('cd' * 16) / 'files' / 'a.mp4').exists()


def test_a_failure_to_apply_is_reported_and_a_retry_resumes_from_what_was_staged(tmp_path):
    attempts = []

    async def apply(manifest, staged):
        attempts.append(sorted(p.name for p in staged.iterdir()))
        if len(attempts) == 1:
            raise RuntimeError('disk full, say')

    pair = Pair(tmp_path, apply=apply)
    files = [pair.add('a.mp4', b'aaaa'), pair.add('b.mp4', b'bbbb')]
    manifest = pair.manifest(files)
    with pytest.raises(TransferError, match='failed to apply'):
        run(send_bundle(pair.a, manifest, pair.src))
    assert 'cd' * 16 not in pair.committed
    pair.sent_files.clear()
    run(send_bundle(pair.a, manifest, pair.src))                   # the retry
    assert pair.sent_files == []                                   # both were staged and verified already
    assert len(attempts) == 2 and 'cd' * 16 in pair.committed


def test_a_transfer_that_was_applied_before_is_recognised_and_not_applied_twice(tmp_path):
    pair = Pair(tmp_path)
    manifest = pair.manifest([pair.add('a.mp4', b'aaaa')])
    run(send_bundle(pair.a, manifest, pair.src))
    run(send_bundle(pair.a, manifest, pair.src))                   # the answer was lost, so the sender asks again
    assert len(pair.applied) == 1


def test_not_enough_room_refuses_before_anything_is_sent(tmp_path):
    pair = Pair(tmp_path, free=1000)
    manifest = pair.manifest([pair.add('a.mp4', b'a' * 100)])
    with pytest.raises(TransferError, match='free space'):
        run(send_bundle(pair.a, manifest, pair.src))
    assert pair.sent_files == []


def test_a_file_that_changes_while_being_sent_aborts_the_transfer(tmp_path):
    pair = Pair(tmp_path)
    entry = pair.add('a.mp4', b'aaaa')
    entry['size'] = 99                                             # the manifest said otherwise
    with pytest.raises(TransferError):
        run(send_bundle(pair.a, pair.manifest([entry]), pair.src))
    assert pair.applied == []


def test_a_hostile_manifest_never_touches_the_disk(tmp_path):
    pair = Pair(tmp_path)
    bad = pair.manifest([{'name': '../escape', 'size': 1, 'sha256': 'a' * 64}])

    async def go():
        await pair.a.send_json(link='transfer-begin', id=bad['id'], manifest=bad)
        with pytest.raises(TransferError, match='Invalid'):
            await pair.a.reply('transfer-want', tid=bad['id'])

    run(go())
    assert not (tmp_path / 'escape').exists() and list(pair.stage.iterdir()) == []


def test_the_receiver_can_be_asked_for_one_transfer_at_a_time(tmp_path):
    pair = Pair(tmp_path)
    manifest = pair.manifest([pair.add('a.mp4', b'aaaa')])

    async def go():
        await pair.a.send_json(link='transfer-begin', id=manifest['id'], manifest=manifest)
        await pair.a.send_json(link='transfer-begin', id='ef' * 16, manifest=manifest)
        err = await asyncio.wait_for(pair.a.replies.get(), 2)
        # the answer to the first one (want) and the refusal of the second arrive in some order
        msgs = [err, await asyncio.wait_for(pair.a.replies.get(), 2)]
        assert any(m['link'] == 'transfer-error' and 'in progress' in m['reason'] for m in msgs)
        pair.b.close()

    run(go())


def test_clean_stale_removes_only_old_folders(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir(), new.mkdir()
    os.utime(old, (1, 1))
    transfer.clean_stale(tmp_path)
    assert not old.exists() and new.exists()
