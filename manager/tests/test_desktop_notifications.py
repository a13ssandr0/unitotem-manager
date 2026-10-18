"""org.freedesktop.Notifications -> notification center: mapping, replacement, rate limit, the answer to a dismissal."""
import pytest

from plugins.unitotem_node.system import desktop_notifications as dn
from utils.notifications import NotificationCenter


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def center(tmp_path, monkeypatch):
    c = NotificationCenter(tmp_path / 'n.json')
    monkeypatch.setattr(dn, 'notifications', c)
    return c


@pytest.fixture
def server(center):
    clock = Clock()
    s = dn.Server('user', clock)
    s.clock = clock
    return s


def shown(center):
    return center.snapshot(['admin'])


def test_plain_text_drops_markup():
    assert dn.plain_text('<b>Disk</b> &amp; <a href="x">more</a>') == 'Disk & more'


def test_mapping(server, center):
    nid = server.raise_notification('udisks', 0, 'Disk full', '<b>sda</b> is full', {'urgency': ('y', 2)})
    [n] = shown(center)
    assert nid > 0 and n['title'] == 'Disk full' and n['text'] == 'sda is full'
    assert n['level'] == 'error' and n['source'] == 'udisks' and n['audience'] == 'admin'


def test_normal_urgency_is_info_and_empty_summary_uses_the_app(server, center):
    server.raise_notification('app', 0, '', 'x', {})
    assert shown(center)[0]['level'] == 'info' and shown(center)[0]['title'] == 'app'


def test_replaces_id_updates(server, center):
    nid = server.raise_notification('app', 0, 'Copying', '10%', {})
    assert server.raise_notification('app', nid, 'Copying', '90%', {}) == nid
    [n] = shown(center)
    assert n['text'] == '90%'


def test_unknown_replaces_id_is_a_new_one(server, center):
    a = server.raise_notification('app', 0, 'a', '', {})
    b = server.raise_notification('app', 4242, 'b', '', {})
    assert a != b and len(shown(center)) == 2


def test_rate_limit_per_application(server, center):
    for i in range(dn.RATE_LIMIT + 10):
        assert server.raise_notification('spam', 0, f's{i}', '', {}) > 0
    server.raise_notification('other', 0, 'ok', '', {})
    titles = [n['title'] for n in shown(center)]
    assert len(titles) == dn.RATE_LIMIT + 1 and 'ok' in titles
    server.clock.now += dn.RATE_WINDOW + 1
    server.raise_notification('spam', 0, 'again', '', {})
    assert 'again' in [n['title'] for n in shown(center)]


def test_ids_are_never_zero(server):
    server._next_id = 0xFFFFFFFF
    assert server._allocate(None) == 1


def test_dismissal_is_reported_to_the_application(server, center, monkeypatch):
    center.listen_closed(lambda item, dismissed: server.closed_in_center(item.key, dismissed))
    emitted = []
    server.notification_closed = type('S', (), {'emit': staticmethod(emitted.append)})()
    nid = server.raise_notification('app', 0, 'hi', '', {})
    center.dismiss_all(['admin'])
    assert emitted == [(nid, dn.CLOSED_DISMISSED)]
