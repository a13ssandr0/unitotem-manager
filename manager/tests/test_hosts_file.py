"""/etc/hosts after a hostname change: exactly one 127.0.1.1 entry, the rest untouched, written atomically."""
import os

from unitotem_node.system.dbus_system import _sync_etc_hosts, render_hosts

DEBIAN = '127.0.0.1\tlocalhost\n127.0.1.1\told\n::1\tlocalhost ip6-localhost ip6-loopback\nff02::1 ip6-allnodes\n'


def test_the_entry_is_replaced():
    assert render_hosts(DEBIAN, 'new', 'old') == DEBIAN.replace('127.0.1.1\told', '127.0.1.1\tnew')


def test_it_does_not_matter_what_the_old_name_was():
    assert '127.0.1.1\tnew' in render_hosts(DEBIAN.replace('old', 'something-else'), 'new', 'old')


def test_duplicates_collapse_into_one():
    out = render_hosts(DEBIAN + '127.0.1.1 old other\n', 'new', 'old')
    assert out.count('127.0.1.1') == 1


def test_an_entry_is_added_after_localhost_when_missing():
    out = render_hosts('127.0.0.1 localhost\n::1 localhost\n', 'new').splitlines()
    assert out == ['127.0.0.1 localhost', '127.0.1.1\tnew', '::1 localhost']


def test_an_empty_file_gets_the_entry():
    assert render_hosts('', 'new') == '127.0.1.1\tnew\n'


def test_the_old_name_leaves_loopback_aliases_only():
    out = render_hosts('127.0.0.1 localhost old\n10.0.0.5 old\n', 'new', 'old')
    assert '127.0.0.1\tlocalhost' in out and '10.0.0.5 old' in out        # a real address is somebody else's business


def test_a_comment_is_not_an_entry():
    out = render_hosts('# 127.0.1.1 old\n127.0.0.1 localhost\n', 'new', 'old')
    assert '# 127.0.1.1 old' in out and '127.0.1.1\tnew' in out


def test_file_is_replaced_atomically_keeping_mode(tmp_path):
    f = tmp_path / 'hosts'
    f.write_text(DEBIAN)
    f.chmod(0o640)
    assert _sync_etc_hosts('old', 'new', str(f))
    assert '127.0.1.1\tnew' in f.read_text() and oct(f.stat().st_mode & 0o777) == '0o640'
    assert not os.path.exists(str(f) + '.unitotem-tmp')


def test_a_missing_file_is_reported(tmp_path):
    assert _sync_etc_hosts('old', 'new', str(tmp_path / 'nope')) is False
