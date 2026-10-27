"""org.freedesktop.Notifications -> notification center: mapping, replacement, rate limit, the answer to a dismissal."""
import pytest

from unitotem_node.system import desktop_notifications as dn
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


def test_a_non_zero_replaces_id_is_returned_as_it_is(server, center):
    # the spec: "If replaces_id is non-zero, the server returns that same value", also for an id it does not know
    a = server.raise_notification('app', 0, 'a', '', {})
    assert server.raise_notification('app', 4242, 'b', '', {}) == 4242
    assert a != 4242 and len(shown(center)) == 2


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


def test_timeouts_follow_the_spec(server, center):
    server.raise_notification('app', 0, 'default', '', {})                       # -1: the level's default
    server.raise_notification('app', 0, 'never', '', {}, [], 0)                   # 0: until clicked
    server.raise_notification('app', 0, 'two s', '', {}, [], 2000)                # > 0: that long
    server.raise_notification('app', 0, 'critical', '', {'urgency': ('y', 2)}, [], 1000)   # critical never expires
    t = {n['title']: n['timeout'] for n in shown(center)}
    assert t == {'default': 6000, 'never': 0, 'two s': 2000, 'critical': 0}


def test_progress_hint_and_flags(server, center):
    server.raise_notification('app', 0, 'Copy', '', {'value': ('i', 40), 'resident': ('b', True)})
    [n] = shown(center)
    assert n['progress'] == 40


def test_actions_default_is_a_click_and_the_rest_buttons(server, center):
    nid = server.raise_notification('app', 0, 'Mail', '', {}, ['default', 'Open', 'reply', 'Reply'])
    [n] = shown(center)
    assert n['default_action'] is True and n['actions'] == [{'id': 'reply', 'label': 'Reply'}]
    told = []
    center.listen_action(lambda item, action: told.append((item.key, action)))
    assert center.invoke(n['id'], 'default', ['admin']) is True
    assert told == [(f'fdo.user.{nid}', 'default')]
    assert shown(center) == []                      # not resident: the click closes it
    assert center.invoke(n['id'], 'default', ['admin']) is False


def test_resident_stays_after_an_action(server, center):
    server.raise_notification('app', 0, 'Mail', '', {'resident': ('b', True)}, ['default', 'Open'])
    [n] = shown(center)
    center.invoke(n['id'], 'default', ['admin'])
    assert len(shown(center)) == 1


def test_expiry_tells_the_sender_and_keeps_it_in_the_bell(server, center):
    import asyncio
    sent = []
    server.notification_closed = type('S', (), {'emit': lambda self, v: sent.append(v)})()

    async def run():
        nid = server.raise_notification('app', 0, 'Quick', '', {}, ['default', 'Open'], 50)
        await asyncio.sleep(0.2)
        return nid

    nid = asyncio.run(run())
    assert sent == [(nid, dn.CLOSED_EXPIRED)]
    [n] = shown(center)
    assert n['default_action'] is False             # the sender cannot be told any more: the id is invalid


def test_transient_leaves_the_bell_when_it_expires(server, center):
    import asyncio
    server.notification_closed = type('S', (), {'emit': lambda self, v: None})()

    async def run():
        server.raise_notification('app', 0, 'Blink', '', {'transient': ('b', True)}, [], 50)
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert shown(center) == []


def test_capabilities_are_only_what_is_implemented():
    from pathlib import Path
    assert "return ['actions', 'body', 'persistence']" in Path(dn.__file__).read_text()
