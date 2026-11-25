"""The updater's progress, as a persistent notification with a bar (see unitotem_node/system/update_progress.py)."""
import asyncio
import calendar
import time

from unitotem_node.system import update_progress as up
from utils.notifications import NotificationCenter


def test_a_persistent_notification_with_a_bar_cannot_be_dismissed(tmp_path):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify('update.progress', 'info', 'Rebuilding slot B', 'Writing', progress=10, persistent=True)
    center.notify('other', 'info', 'Something')
    assert center.set_progress('update.progress', 55, 'Writing, about 30 s left')
    perms = ['admin']
    shown = {n['title']: n for n in center.snapshot(perms)}
    assert shown['Rebuilding slot B']['progress'] == 55 and shown['Rebuilding slot B']['persistent']
    center.dismiss_all(perms)
    left = [n['title'] for n in center.snapshot(perms)]
    assert left == ['Rebuilding slot B']
    center.dismiss([shown['Rebuilding slot B']['id']], perms)
    assert [n['title'] for n in center.snapshot(perms)] == ['Rebuilding slot B']
    assert center.resolve('update.progress')
    assert center.snapshot(perms) == []


def test_moving_the_bar_does_not_make_it_unread_again(tmp_path):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify('k', 'info', 't', progress=1, persistent=True)
    item = center.snapshot(['admin'])[0]
    center.mark_read([item['id']], ['admin'])
    center.set_progress('k', 2)
    assert center.snapshot(['admin'])[0]['read'] is True
    assert center.set_progress('missing', 5) is False


def test_watcher_follows_an_operation_from_start_to_end(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    monkeypatch.setattr(up, 'notifications', center)
    answers = []
    pushed = []

    async def status():
        return answers.pop(0)

    monkeypatch.setattr(up.updater, 'status', status)

    async def on_status(s):
        pushed.append(s)

    async def scenario():
        watcher = up.UpdateProgress()
        watcher._loop = asyncio.get_running_loop()
        watcher._on_status = on_status
        running = {'supported': True, 'other_slot': 'B',
                   'op': {'pid': 1, 'progress': {'op': 'repair', 'phase': 'write', 'percent': 40, 'eta_s': 20}}}
        answers.append(running)
        await watcher.refresh()
        first = center.snapshot(['admin'])[0]
        assert first['title'] == 'Rebuilding slot B' and first['progress'] == 40 and first['persistent']
        running2 = {**running, 'op': {'pid': 1, 'progress': {'op': 'repair', 'phase': 'verify', 'percent': 80}}}
        answers.append(running2)
        await watcher.refresh()
        assert center.snapshot(['admin'])[0]['progress'] == 80
        answers.append({'supported': True, 'other_slot': 'B', 'op': None,
                        'last_result': {'op': 'repair', 'result': 'ok', 'slot': 'B'}})
        await watcher.refresh()
        titles = [n['title'] for n in center.snapshot(['admin'])]
        assert titles == ['Slot B rebuilt']          # the bar is gone, what came of it is told
        assert len(pushed) == 3

    asyncio.run(scenario())


def test_a_bar_left_by_a_dead_manager_is_removed_at_start(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'info', 'Downloading the update', progress=30, persistent=True)
    monkeypatch.setattr(up, 'notifications', center)

    async def status():
        return {'supported': True, 'op': None}

    monkeypatch.setattr(up.updater, 'status', status)

    async def scenario():
        watcher = up.UpdateProgress()
        watcher._loop = asyncio.get_running_loop()
        await watcher.refresh()

    asyncio.run(scenario())
    assert center.snapshot(['admin']) == []


def test_rebuild_titles_tell_damage_from_an_incomplete_system():
    up = __import__('unitotem_node.system.update_progress', fromlist=['x'])
    assert up.describe('repair', 'write', 'B', 'fsck')[0] == 'Rebuilding slot B after damage'
    assert up.describe('repair', 'write', 'B', 'bootfile:kernel8.img')[0] == 'Rebuilding slot B after damage'
    assert up.describe('repair', 'write', 'B', 'empty')[0] == 'Rebuilding slot B (incomplete system)'
    assert up.describe('repair', 'write', 'B')[0] == 'Rebuilding slot B'
    assert up.describe('fetch', 'download', 'B')[0] == 'Downloading the update'
    assert up.cause_text('fsck') == 'its filesystem is damaged' and up.cause_text('bootfile:x') == 'its boot file x is missing'


def test_forced_copy_has_its_own_title():
    assert up.describe('repair', 'write', 'B', None, 'A')[0] == 'Copying slot A to slot B'
    assert up.describe('repair', 'write', 'B', 'fsck', None)[0] == 'Rebuilding slot B after damage'


# ── the bar's life: level, in-place updates, the result, restarts ────────────

def feed(monkeypatch, center):
    """Routes the watcher to `center` and to a queue of statuses (the updater is never run)."""
    answers = []
    monkeypatch.setattr(up, 'notifications', center)

    async def status():
        return answers.pop(0)

    monkeypatch.setattr(up.updater, 'status', status)
    return answers


def watcher():
    w = up.UpdateProgress()
    w._loop = asyncio.get_running_loop()
    return w


async def tick(w, answers, status):
    answers.append(status)
    await w.refresh()


def running(name='repair', phase='write', percent=None, state=None, cause=None, eta_s=None, slot='A', other='B', **extra):
    progress = {'op': name, 'phase': phase}
    for key, value in (('percent', percent), ('state', state), ('cause', cause), ('eta_s', eta_s)):
        if value is not None:
            progress[key] = value
    return {'supported': True, 'slot': slot, 'other_slot': other, 'op': {'pid': 1, 'progress': progress}, **extra}


def idle(last=None):
    return {'supported': True, 'slot': 'A', 'other_slot': 'B', 'op': None, 'last_result': last}


def test_the_bar_is_an_error_with_a_bar_and_persistent(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, running(percent=10))

    asyncio.run(scenario())
    (bar,) = center.snapshot(['admin'])
    assert bar['level'] == 'error' and bar['persistent'] is True and bar['progress'] == 10
    assert bar['title'] == 'Copying slot A to slot B'


def test_the_percentage_moves_in_place_and_stays_read(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        w = watcher()
        await tick(w, answers, running(percent=10))
        first = center.snapshot(['admin'])[0]
        center.mark_read([first['id']], ['admin'])
        await tick(w, answers, running(percent=60, eta_s=20))
        return first

    first = asyncio.run(scenario())
    (bar,) = center.snapshot(['admin'])
    assert bar['id'] == first['id'] and bar['progress'] == 60 and bar['read'] is True
    assert bar['text'] == 'about 20 s left'


def test_the_bar_cannot_be_dismissed_while_running(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, running(percent=10))

    asyncio.run(scenario())
    bar_id = center.snapshot(['admin'])[0]['id']
    center.dismiss_all(['admin'])
    center.dismiss([bar_id], ['admin'])
    center.dismiss([bar_id], ['admin'], user='someone')
    assert [n['id'] for n in center.snapshot(['admin'])] == [bar_id]


def test_a_successful_copy_is_replaced_by_one_short_result(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        w = watcher()
        await tick(w, answers, running(percent=90))
        await tick(w, answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B'}))

    asyncio.run(scenario())
    (result,) = center.snapshot(['admin'])
    assert result['title'] == 'Slot B copied' and result['level'] == 'info' and result['persistent'] is False
    assert result['text'] == 'Slot B is now a copy of slot A.' and result['progress'] is None


def test_a_failed_copy_is_an_error_with_the_updater_text(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        w = watcher()
        await tick(w, answers, running(percent=5))
        await tick(w, answers, idle({'op': 'repair', 'result': 'failed', 'slot': 'B', 'error': 'No space left'}))

    asyncio.run(scenario())
    (result,) = center.snapshot(['admin'])
    assert result['title'] == 'Copying the slot failed' and result['level'] == 'error' and result['text'] == 'No space left'
    assert result['progress'] is None


def test_the_bar_survives_a_restart_and_resumes(tmp_path, monkeypatch):
    path = tmp_path / 'n.json'
    answers = feed(monkeypatch, NotificationCenter(path=path))

    async def first_run():
        await tick(watcher(), answers, running(percent=30))

    asyncio.run(first_run())
    before = NotificationCenter(path=path).snapshot(['admin'])[0]['id']
    center = NotificationCenter(path=path)
    answers = feed(monkeypatch, center)

    async def after_restart():
        await tick(watcher(), answers, running(percent=70))

    asyncio.run(after_restart())
    (bar,) = center.snapshot(['admin'])
    assert bar['id'] == before and bar['progress'] == 70 and bar['level'] == 'error'


def test_a_bar_left_at_another_level_is_raised_again_at_error(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'info', 'Downloading the update', progress=30, persistent=True)
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, running(name='fetch', phase='download', percent=31))

    asyncio.run(scenario())
    (bar,) = center.snapshot(['admin'])
    assert bar['level'] == 'error' and bar['progress'] == 31 and bar['title'] == 'Downloading the update'
    assert center.get(up.KEY)['level'] == 'error'


def test_an_operation_running_at_boot_raises_the_bar(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, running(name='fetch', phase='download', percent=0))

    asyncio.run(scenario())
    (bar,) = center.snapshot(['admin'])
    assert bar['title'] == 'Downloading the update' and bar['level'] == 'error' and bar['persistent'] is True


def test_a_final_state_is_looked_at_once_more(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)
    monkeypatch.setattr(up, 'LATE_REFRESH', 0.01)

    async def scenario():
        w = watcher()
        # the updater wrote `done` but still held its lock: the status is read again after the lock is released
        await tick(w, answers, running(percent=100, state='done'))
        assert w._late is not None
        answers.append(idle({'op': 'repair', 'result': 'ok', 'slot': 'B'}))
        await asyncio.sleep(0.1)
        assert w._late is None and answers == []

    asyncio.run(scenario())
    assert [n['title'] for n in center.snapshot(['admin'])] == ['Slot B copied']


def test_the_late_look_is_not_repeated_while_the_state_stays_final(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)
    monkeypatch.setattr(up, 'LATE_REFRESH', 0.01)

    async def scenario():
        w = watcher()
        await tick(w, answers, running(percent=100, state='done'))
        answers.append(running(percent=100, state='done'))
        await asyncio.sleep(0.1)
        assert w._late is None and answers == []

    asyncio.run(scenario())


def test_a_downloaded_update_is_told(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)
    monkeypatch.setitem(up.updater.availability, 'version', '2026.12.1')

    async def scenario():
        w = watcher()
        await tick(w, answers, running(name='fetch', phase='download', percent=100))
        await tick(w, answers, idle({'op': 'fetch', 'result': 'ok'}))

    asyncio.run(scenario())
    (result,) = center.snapshot(['admin'])
    assert result['title'] == 'Update downloaded' and result['level'] == 'info'
    assert result['text'] == 'Version 2026.12.1 is ready to install'


def test_a_failed_download_is_an_error(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        w = watcher()
        await tick(w, answers, running(name='fetch', phase='download', percent=12))
        await tick(w, answers, idle({'op': 'fetch', 'result': 'failed', 'error': 'network'}))

    asyncio.run(scenario())
    (result,) = center.snapshot(['admin'])
    assert result['title'] == 'Update failed' and result['level'] == 'error' and result['text'] == 'network'


def test_after_a_restart_the_result_of_the_operation_is_told(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'error', 'Copying slot A to slot B', progress=50, persistent=True)
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B'}))

    asyncio.run(scenario())
    assert center.get(up.KEY) is None
    assert [n['title'] for n in center.snapshot(['admin'])] == ['Slot B copied']


def test_after_a_restart_an_older_result_is_not_told_again(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'error', 'Copying slot A to slot B', progress=50, persistent=True)
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B', 'at': '2001-01-01T00:00:00Z'}))

    asyncio.run(scenario())
    assert center.get(up.KEY) is None and center.snapshot(['admin']) == []


def test_after_a_restart_a_result_not_older_than_the_bar_is_told(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'error', 'Copying slot A to slot B', progress=50, persistent=True)
    answers = feed(monkeypatch, center)
    later = time.strftime(up.UPDATED_AT, time.gmtime(time.time() + 60))

    async def scenario():
        await tick(watcher(), answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B', 'at': later}))

    asyncio.run(scenario())
    assert [n['title'] for n in center.snapshot(['admin'])] == ['Slot B copied']


def test_after_a_restart_an_unreadable_result_time_is_told(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'error', 'Copying slot A to slot B', progress=50, persistent=True)
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B', 'at': 'yesterday'}))

    asyncio.run(scenario())
    assert [n['title'] for n in center.snapshot(['admin'])] == ['Slot B copied']


def test_the_result_time_is_utc_text_or_unknown():
    assert up.result_time('1970-01-01T00:00:10Z') == 10.0
    assert up.result_time('2026-11-25T12:00:00Z') == calendar.timegm((2026, 11, 25, 12, 0, 0))
    assert up.result_time('2026-11-25 12:00:00') is None
    assert up.result_time(1795000000) is None and up.result_time(None) is None


def test_the_write_shows_its_size_and_a_repair_write_does_not_repeat_the_title():
    assert up.size_caption({'bytes': 250_000_000, 'total': 1_000_000_000}) == '250 / 1000 MB'
    assert up.size_caption({'bytes': 2_000_000, 'total': 1_000_000}) is None
    assert up.size_caption({'bytes': 1, 'total': 0}) is None and up.size_caption({'bytes': True, 'total': 5}) is None
    assert up.describe('repair', 'write', 'B', None, 'A', '250 / 1000 MB') == ('Copying slot A to slot B', '250 / 1000 MB')
    assert up.describe('repair', 'verify', 'B', None, 'A') == ('Copying slot A to slot B', 'Verifying')
    assert up.describe('repair', 'write', 'B') == ('Rebuilding slot B', '')


def test_the_bar_text_of_a_write_is_its_size(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)
    write = running(percent=25, eta_s=30, copy_from=None)
    write['op']['progress'].update(bytes=250_000_000, total=1_000_000_000)

    async def scenario():
        await tick(watcher(), answers, write)

    asyncio.run(scenario())
    assert center.snapshot(['admin'])[0]['text'] == '250 / 1000 MB, about 30 s left'


def test_the_cause_chooses_copy_or_rebuild_title(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)
    cases = {None: 'Copying slot A to slot B', 'first-boot': 'Copying slot A to slot B', 'unprepared': 'Copying slot A to slot B',
             'label': 'Rebuilding slot B after damage', 'fsck': 'Rebuilding slot B after damage',
             'mount': 'Rebuilding slot B after damage', 'bootfile:kernel8.img': 'Rebuilding slot B after damage',
             'empty': 'Rebuilding slot B (incomplete system)'}

    async def scenario():
        w = watcher()
        for cause, title in cases.items():
            await tick(w, answers, running(percent=5, cause=cause))
            assert center.snapshot(['admin'])[0]['title'] == title, cause

    asyncio.run(scenario())


def test_a_copy_from_the_status_is_a_copy_whatever_the_cause(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, running(percent=5, cause='fsck', copy_from='A'))

    asyncio.run(scenario())
    assert center.snapshot(['admin'])[0]['title'] == 'Copying slot A to slot B'


def test_after_a_restart_a_rebuild_after_damage_is_told_as_a_rebuild(tmp_path, monkeypatch):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify(up.KEY, 'error', 'Rebuilding slot B', progress=50, persistent=True)
    answers = feed(monkeypatch, center)

    async def scenario():
        await tick(watcher(), answers, idle({'op': 'repair', 'result': 'ok', 'slot': 'B', 'cause': 'fsck'}))

    asyncio.run(scenario())
    assert [n['title'] for n in center.snapshot(['admin'])] == ['Slot B rebuilt']
    assert 'because its filesystem is damaged.' in center.snapshot(['admin'])[0]['text']


def test_a_copy_is_told_by_its_cause_and_the_running_slot():
    assert up.copy_source(None, 'A') == 'A'
    assert up.copy_source('first-boot', 'A') == 'A'
    for cause in ('label', 'fsck', 'mount', 'empty', 'bootfile:kernel8.img'):
        assert up.copy_source(cause, 'A') is None
    assert up.phase_text('repair', 'write') == '' and up.phase_text('fetch', 'write') == 'Writing'
