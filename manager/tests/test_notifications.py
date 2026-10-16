"""The notification center: keys (dedup, resolve), bounds, persistence and compatibility, who sees what, the push."""
import asyncio
import json
import threading

import pytest

from utils.notifications import MAX_TEXT, MAX_TITLE, NotificationCenter, visible_to


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


@pytest.fixture
def path(tmp_path):
    return tmp_path / 'state' / 'notifications.json'


@pytest.fixture
def center(path):
    return NotificationCenter(path, Clock(), max_items=5)


def titles(center, perms=('admin',)):
    return [n['title'] for n in center.snapshot(perms)]


# ── keys ─────────────────────────────────────────────────────────────────────

def test_the_same_key_and_content_is_not_raised_twice(center):
    first = center.notify('k', 'warning', 'Disk', 'almost full')
    again = center.notify('k', 'warning', 'Disk', 'almost full')
    assert first == again
    assert len(center.snapshot(['admin'])) == 1


def test_the_same_key_with_new_content_updates_and_is_unread_again(center):
    first = center.notify('k', 'warning', 'Disk', '90%')
    center.mark_read([first], ['admin'])
    assert center.snapshot(['admin'])[0]['read'] is True
    center.notify('k', 'error', 'Disk', '99%')
    [shown] = center.snapshot(['admin'])
    assert (shown['id'], shown['level'], shown['text'], shown['read']) == (first, 'error', '99%', False)


def test_resolve_removes_by_key_and_says_whether_there_was_one(center):
    center.notify('k', 'info', 'A')
    assert center.resolve('k') is True
    assert center.resolve('k') is False
    assert center.snapshot(['admin']) == []


def test_without_a_key_every_call_is_a_new_notification(center):
    center.notify(None, 'info', 'A')
    center.notify(None, 'info', 'A')
    assert titles(center) == ['A', 'A']


def test_a_dismissed_notification_stays_dismissed_until_it_changes_or_resolves(center):
    nid = center.notify('k', 'warning', 'Disk', '90%')
    center.dismiss([nid], ['admin'])
    assert center.snapshot(['admin']) == []
    center.notify('k', 'warning', 'Disk', '90%')           # the periodic check says it again: no nagging
    assert center.snapshot(['admin']) == []
    center.notify('k', 'warning', 'Disk', '95%')           # but a change is news
    assert titles(center) == ['Disk']
    center.dismiss([nid], ['admin'])
    center.resolve('k')                                    # problem gone, so a later one is a new problem
    center.notify('k', 'warning', 'Disk', '95%')
    assert titles(center) == ['Disk']


def test_dismissing_one_without_a_key_removes_it(center):
    nid = center.notify(None, 'info', 'A')
    center.dismiss([nid], ['admin'])
    assert center._items == []


# ── bounds and sanitising ────────────────────────────────────────────────────

def test_the_list_is_bounded_and_drops_what_nobody_sees_first_then_the_read_then_the_oldest(center):
    ids = [center.notify(f'k{i}', 'info', f'n{i}') for i in range(5)]
    center.mark_read([ids[3]], ['admin'])
    center.dismiss([ids[4]], ['admin'])
    center.notify('new1', 'info', 'new1')       # the dismissed one goes
    assert 'n4' not in {i.title for i in center._items}
    center.notify('new2', 'info', 'new2')       # then the read one
    assert 'n3' not in {i.title for i in center._items}
    center.notify('new3', 'info', 'new3')       # then the oldest unread
    assert 'n0' not in {i.title for i in center._items}
    assert len(center._items) == 5


def test_text_is_clipped_stripped_of_control_characters_and_never_markup_aware(center):
    center.notify('k', 'info', 'T' * 5000, '<script>alert(1)</script>\x00\x1b[31m' + 'x' * 5000)
    [shown] = center.snapshot(['admin'])
    assert len(shown['title']) == MAX_TITLE and len(shown['text']) == MAX_TEXT
    assert '\x00' not in shown['text'] and '\x1b' not in shown['text']
    assert shown['text'].startswith('<script>')       # kept as text: the page escapes it


@pytest.mark.parametrize('link', ['//evil.example/x', 'https://evil.example', 'javascript:alert(1)', '/\\evil', '/a b', 5])
def test_a_link_can_only_be_a_page_of_this_web_ui(center, link):
    center.notify('k', 'info', 'A', link=link)
    assert center.snapshot(['admin'])[0]['link'] is None


def test_a_good_link_is_kept(center):
    center.notify('k', 'info', 'A', link='/settings/updates')
    assert center.snapshot(['admin'])[0]['link'] == '/settings/updates'


def test_bad_arguments_are_refused_without_raising(center):
    assert center.notify('k', 'catastrophe', 'A') is None
    assert center.notify('k', 'info', 'A', audience='root') is None
    assert center.snapshot(['admin']) == []


# ── audience ─────────────────────────────────────────────────────────────────

def test_who_sees_what(center):
    center.notify('a', 'info', 'for everybody')
    center.notify('b', 'info', 'for the scheduler', audience='scheduler')
    center.notify('c', 'info', 'for admins', audience='admin')
    assert set(titles(center, ['admin'])) == {'for everybody', 'for the scheduler', 'for admins'}
    assert set(titles(center, ['scheduler'])) == {'for everybody', 'for the scheduler'}
    assert set(titles(center, ['power', 'audio'])) == {'for everybody'}
    assert set(titles(center, [])) == {'for everybody'}


def test_a_user_cannot_touch_what_they_cannot_see(center):
    secret = center.notify('c', 'info', 'for admins', audience='admin')
    center.mark_read([secret], ['scheduler'])
    center.dismiss([secret], ['scheduler'])
    center.dismiss_all(['scheduler'])
    [shown] = center.snapshot(['admin'])
    assert shown['read'] is False


def test_dismiss_all_only_takes_what_the_user_sees(center):
    center.notify('a', 'info', 'public')
    center.notify('c', 'info', 'for admins', audience='admin')
    center.dismiss_all(['scheduler'])
    assert titles(center, ['admin']) == ['for admins']


def test_visible_to_accepts_enum_members():
    from utils.models.user import UserPerms
    from utils.notifications import Notification
    assert visible_to(Notification(audience='scheduler'), {UserPerms.scheduler})
    assert not visible_to(Notification(audience='admin'), {UserPerms.scheduler})
    assert visible_to(Notification(audience='admin'), {UserPerms.admin})


def test_newest_first(center):
    center.notify('a', 'info', 'old')
    center.notify('b', 'info', 'new')
    assert titles(center) == ['new', 'old']


# ── persistence ──────────────────────────────────────────────────────────────

def test_round_trip_across_a_restart(path):
    one = NotificationCenter(path, Clock())
    nid = one.notify('k', 'error', 'Broken', 'text', source='s', audience='admin', link='/x')
    one.mark_read([nid], ['admin'])
    two = NotificationCenter(path, Clock())
    [shown] = two.snapshot(['admin'])
    assert shown == {'id': nid, 'level': 'error', 'title': 'Broken', 'text': 'text', 'source': 's',
                     'audience': 'admin', 'link': '/x', 'created': shown['created'], 'updated': shown['updated'],
                     'read': True}
    two.notify('k', 'error', 'Broken', 'text', source='s', audience='admin', link='/x')     # still the same one
    assert len(two.snapshot(['admin'])) == 1


def test_an_unknown_field_and_a_missing_one_are_both_fine(path):
    """Rollback rule: a newer release's extra fields are ignored, an older one's missing fields get defaults."""
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 7, 'future': True, 'items': [
        {'id': 'a', 'key': 'k', 'title': 'From the future', 'level': 'info', 'created': 1, 'updated': 1,
         'color': 'blue', 'actions': [1, 2]},
        {'title': 'Minimal'},
    ]}))
    center = NotificationCenter(path, Clock())
    assert set(titles(center)) == {'From the future', 'Minimal'}
    center.notify('x', 'info', 'Another')       # writing back drops what this release does not know
    assert 'color' not in json.dumps(json.loads(path.read_text()))


def test_an_unknown_audience_or_level_in_the_file_is_read_safely(path):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'items': [{'title': 'x', 'audience': 'superuser', 'level': 'fatal'}]}))
    center = NotificationCenter(path, Clock())
    assert center.snapshot(['scheduler']) == []         # strictest reading
    assert center.snapshot(['admin'])[0]['level'] == 'info'


@pytest.mark.parametrize('content', ['', 'not json', '[]', '{"items": 5}', '{"items": [1, "a", null]}'])
def test_a_damaged_file_means_an_empty_list_not_a_crash(path, content):
    path.parent.mkdir(parents=True)
    path.write_text(content)
    center = NotificationCenter(path, Clock())
    assert center.snapshot(['admin']) == []
    center.notify('k', 'info', 'works')
    assert titles(center) == ['works']


def test_a_disk_that_cannot_be_written_does_not_stop_a_producer(tmp_path):
    blocker = tmp_path / 'file'
    blocker.write_text('')
    center = NotificationCenter(blocker / 'sub' / 'n.json', Clock())
    assert center.notify('k', 'info', 'still here')
    assert titles(center) == ['still here']


def test_the_file_is_replaced_atomically(path):
    center = NotificationCenter(path, Clock())
    center.notify('k', 'info', 'A')
    assert not path.with_name(path.name + '.tmp').exists()
    assert json.loads(path.read_text())['items'][0]['title'] == 'A'


# ── threads and the push ─────────────────────────────────────────────────────

def test_producers_in_other_threads_do_not_lose_anything(path):
    center = NotificationCenter(path, Clock(), max_items=1000)

    def work(n):
        for i in range(50):
            center.notify(f'{n}.{i}', 'info', f'{n}.{i}')

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(center.snapshot(['admin'])) == 400
    assert len(NotificationCenter(path, Clock(), max_items=1000).snapshot(['admin'])) == 400


def test_changes_are_pushed_once_per_burst_even_from_another_thread(path):
    async def scenario():
        center = NotificationCenter(path, Clock())
        calls = []

        async def push():
            calls.append(1)

        center.bind(asyncio.get_running_loop(), push)
        center.notify('a', 'info', 'A')
        center.notify('b', 'info', 'B')
        await asyncio.sleep(0.05)
        assert len(calls) == 1
        await asyncio.get_running_loop().run_in_executor(None, center.resolve, 'a')
        await asyncio.sleep(0.05)
        assert len(calls) == 2
        center.notify('b', 'info', 'B')         # nothing changed: nothing pushed
        await asyncio.sleep(0.05)
        assert len(calls) == 2

    asyncio.run(scenario())


def test_a_failing_push_does_not_break_the_producer_or_the_next_push(path):
    async def scenario():
        center = NotificationCenter(path, Clock())
        calls = []

        async def push():
            calls.append(1)
            raise RuntimeError('socket closed')

        center.bind(asyncio.get_running_loop(), push)
        center.notify('a', 'info', 'A')
        await asyncio.sleep(0.05)
        center.notify('b', 'info', 'B')
        await asyncio.sleep(0.05)
        assert len(calls) == 2

    asyncio.run(scenario())
