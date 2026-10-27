"""The updater's progress, as a persistent notification with a bar (see unitotem_node/system/update_progress.py)."""
import asyncio

from unitotem_node.system import update_progress as up
from utils.notifications import NotificationCenter


def test_a_persistent_notification_with_a_bar_cannot_be_dismissed(tmp_path):
    center = NotificationCenter(path=tmp_path / 'n.json')
    center.notify('update.progress', 'info', 'Repairing slot B', 'Writing', progress=10, persistent=True)
    center.notify('other', 'info', 'Something')
    assert center.set_progress('update.progress', 55, 'Writing, about 30 s left')
    perms = ['admin']
    shown = {n['title']: n for n in center.snapshot(perms)}
    assert shown['Repairing slot B']['progress'] == 55 and shown['Repairing slot B']['persistent']
    center.dismiss_all(perms)
    left = [n['title'] for n in center.snapshot(perms)]
    assert left == ['Repairing slot B']
    center.dismiss([shown['Repairing slot B']['id']], perms)
    assert [n['title'] for n in center.snapshot(perms)] == ['Repairing slot B']
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
        assert first['title'] == 'Repairing slot B' and first['progress'] == 40 and first['persistent']
        running2 = {**running, 'op': {'pid': 1, 'progress': {'op': 'repair', 'phase': 'verify', 'percent': 80}}}
        answers.append(running2)
        await watcher.refresh()
        assert center.snapshot(['admin'])[0]['progress'] == 80
        answers.append({'supported': True, 'other_slot': 'B', 'op': None,
                        'last_result': {'op': 'repair', 'result': 'ok', 'slot': 'B'}})
        await watcher.refresh()
        titles = [n['title'] for n in center.snapshot(['admin'])]
        assert titles == ['Slot B repaired']          # the bar is gone, what came of it is told
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
