"""What arrives from the network is whatever any device on it decided to send: the announcement parser is strict and total."""
import pytest

from utils.remote.discovery import MAX_NAME, parse_announcement, usable_address

ID = 'ab' * 16


def props(**over):
    base = {b'v': b'1', b'id': ID.encode(), b'name': b'lobby', b'web': b'443', b'link': b'4443'}
    base.update({k.encode(): v for k, v in over.items() if v is not None})
    for k, v in over.items():
        if v is None:
            base.pop(k.encode(), None)
    return base


def test_a_well_formed_announcement():
    found = parse_announcement(props(), ['192.168.1.20'], 443)
    assert (found.node_id, found.name, found.host, found.port, found.link_port) == (ID, 'lobby', '192.168.1.20', 443, 4443)


def test_the_first_usable_address_is_taken_and_loopback_and_junk_skipped():
    found = parse_announcement(props(), ['127.0.0.1', 'fe80::1', 'not an address', '224.0.0.1', '10.1.2.3'], 443)
    assert found.host == '10.1.2.3'
    assert parse_announcement(props(), ['127.0.0.1', '::1'], 443) is None
    assert parse_announcement(props(), [], 443) is None


@pytest.mark.parametrize('bad', [
    {'v': b'2'}, {'v': None}, {'id': None}, {'id': b'short'}, {'id': b'AB' * 16}, {'id': b'zz' * 16},
    {'id': ('ab' * 16 + '\n').encode()},
])
def test_wrong_version_or_identifier_is_ignored(bad):
    assert parse_announcement(props(**bad), ['192.168.1.20'], 443) is None


def test_names_are_cleaned_and_bounded():
    assert parse_announcement(props(name=b'a\x00b\x1b[31m\r\n'), ['10.0.0.2'], 443).name == 'ab[31m'
    assert len(parse_announcement(props(name=b'x' * 5000), ['10.0.0.2'], 443).name) == MAX_NAME
    assert parse_announcement(props(name=b'\xff\xfe'), ['10.0.0.2'], 443).name == ID[:8]     # not UTF-8: no name
    assert parse_announcement(props(name=None), ['10.0.0.2'], 443).name == ID[:8]


def test_ports_fall_back_and_are_range_checked():
    assert parse_announcement(props(web=b'99999'), ['10.0.0.2'], 8443).port == 8443
    assert parse_announcement(props(web=b'abc'), ['10.0.0.2'], 8443).port == 8443
    assert parse_announcement(props(web=b'0'), ['10.0.0.2'], None) is None
    assert parse_announcement(props(link=b'-5'), ['10.0.0.2'], 443).link_port == 4443


def test_garbage_never_raises():
    assert parse_announcement({}, ['10.0.0.2'], 443) is None
    assert parse_announcement(None, None, None) is None
    assert parse_announcement({b'v': None, b'id': None}, ['10.0.0.2'], 443) is None
    assert parse_announcement({1: 2, None: b'x'}, ['10.0.0.2'], 443) is None


def test_usable_address():
    assert usable_address('192.168.0.5') == '192.168.0.5'
    for bad in ('0.0.0.0', '127.0.0.1', '239.1.1.1', '240.0.0.1', '::1', '2001:db8::1', 'x', ''):
        assert usable_address(bad) is None
