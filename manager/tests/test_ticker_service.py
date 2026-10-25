"""The scheduler of the feeds (fake clock and fetch), what gets pushed and when, notifications, the backup part."""
import asyncio
import functools
import json

import pytest

import utils.ticker as ticker
from utils.models.ticker import TickerSettings, TickerSource
from utils.notifications import notifications
from utils.ticker import fetcher, push
from utils.ticker.service import FAILS_BEFORE_NOTIFYING, TickerService, notification_key
from utils.ticker.store import TickerStore
from utils.models.layout import WindowLayouts
from utils.ticker.windows import WindowTickers


def aio(fn):
    """Runs a coroutine test on a fresh loop (pytest-asyncio is not a dependency of the project)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # not asyncio.run: it would unset the current loop that the modules imported later (conftest) rely on
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(fn(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


class Clock:
    def __init__(self, now=10_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return TickerStore(tmp_path / 'sources.json', tmp_path / 'cache.json', clock=clock)


class Fake:
    """A fetch function with a script per URL: a list of results (items, 'same' or an exception), the last repeating."""

    def __init__(self):
        self.script: dict[str, list] = {}
        self.calls: list[str] = []

    async def __call__(self, url, etag=None, last_modified=None, *, insecure=False, now=None):
        self.calls.append(url)
        steps = self.script[url]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        if step == 'same':
            return fetcher.FetchResult(not_modified=True)
        return fetcher.FetchResult(items=step, etag='e')


def it(i, t=100, link=None):
    return {'id': str(i), 'time': t, 'title': f't{i}', 'summary': '', 'link': link if link is not None else f'http://x/{i}'}


async def settle(n=30):
    for _ in range(n):
        await asyncio.sleep(0)


async def start(service):
    task = asyncio.get_running_loop().create_task(service.run())
    await settle()
    return task


async def stop(service, task):
    service.stop()
    await asyncio.wait_for(task, 2)


@aio
async def test_a_new_source_is_fetched_at_once_and_then_after_its_interval(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [[it(1)]]
    service = TickerService(store, fetch=fake, clock=clock)
    seen = []
    service.on_items = lambda items: seen.append(items)
    a = store.add(TickerSource(url='http://a/rss', interval=300))
    task = await start(service)
    assert fake.calls == ['http://a/rss'] and len(seen) == 1
    assert store.state[a.id].next_due == clock.now + 300
    # nothing happens until the interval has passed: no polling
    await settle(200)
    assert len(fake.calls) == 1
    # the clock moves and the loop is woken up the way its timer would
    clock.now += 300
    service.poke()
    await settle()
    assert len(fake.calls) == 2
    await stop(service, task)


@aio
async def test_the_loop_sleeps_for_the_real_time_until_the_next_source(store):
    """Real clock, a source due in 0.3 s: the task wakes up by itself (it is a timer, not a poll)."""
    fake = Fake()
    fake.script['http://a/rss'] = [[it(1)]]
    service = TickerService(store, fetch=fake)
    a = store.add(TickerSource(url='http://a/rss', interval=60))
    task = await start(service)
    assert len(fake.calls) == 1
    store.state[a.id].next_due = service._clock() + 0.3
    service.poke()
    await asyncio.sleep(0.1)
    assert len(fake.calls) == 1
    await asyncio.sleep(0.5)
    assert len(fake.calls) == 2
    await stop(service, task)


@aio
async def test_listeners_only_hear_about_a_change(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [[it(1)], [it(1)], 'same', [it(1), it(2)]]
    service = TickerService(store, fetch=fake, clock=clock)
    pushes = []
    service.on_items = lambda items: pushes.append(len(items))
    store.add(TickerSource(url='http://a/rss', interval=60))
    task = await start(service)
    for _ in range(3):
        clock.now += 60
        service.poke()
        await settle()
    assert len(fake.calls) == 4
    assert pushes == [1, 2]            # the same list twice and a 304 are not events
    await stop(service, task)


@aio
async def test_one_failing_source_never_stops_the_others(store, clock):
    fake = Fake()
    fake.script['http://bad/rss'] = [fetcher.FetchError('The server answered HTTP 500')]
    fake.script['http://crash/rss'] = [RuntimeError('boom')]
    fake.script['http://good/rss'] = [[it(1), it(2)]]
    service = TickerService(store, fetch=fake, clock=clock)
    for u in ('bad', 'crash', 'good'):
        store.add(TickerSource(url=f'http://{u}/rss', interval=600))
    task = await start(service)
    assert sorted(fake.calls) == ['http://bad/rss', 'http://crash/rss', 'http://good/rss']
    assert [i['title'] for i in store.merged()] == ['t1', 't2']
    states = {s.url: store.state[s.id] for s in store.sources}
    assert states['http://bad/rss'].error == 'The server answered HTTP 500'
    assert states['http://crash/rss'].error == 'Unexpected error'
    assert states['http://good/rss'].error == ''
    await stop(service, task)


@aio
async def test_notification_after_repeated_failures_once_and_resolved_on_recovery(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [fetcher.FetchError('Cannot connect to the server')] * 5 + [[it(1)]]
    service = TickerService(store, fetch=fake, clock=clock)
    a = store.add(TickerSource(url='http://a/rss', name='ANSA', interval=3600))
    task = await start(service)

    def mine():
        return [n for n in notifications.snapshot(['admin']) if 'ANSA' in n['title']]

    assert FAILS_BEFORE_NOTIFYING == 2
    assert mine() == []                                         # one blip is not news
    clock.now += 60                                             # the retry comes after 60 s, not an hour
    service.poke()
    await settle()
    assert len(mine()) == 1
    for _ in range(3):
        clock.now += 3600
        service.poke()
        await settle()
    assert len(mine()) == 1                                     # still one, not one per failure
    clock.now += 3600
    service.poke()
    await settle()
    assert fake.calls.count('http://a/rss') == 6 and mine() == [] and store.state[a.id].fails == 0
    assert notification_key(a.id) == f'ticker.source.{a.id}'
    await stop(service, task)


@aio
async def test_failure_notification_is_for_admins_only(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [fetcher.FetchError('x')]
    service = TickerService(store, fetch=fake, clock=clock)
    store.add(TickerSource(url='http://a/rss'))
    task = await start(service)
    clock.now += 60
    service.poke()
    await settle()
    assert len(notifications.snapshot(['admin'])) == 1
    assert notifications.snapshot(['scheduler']) == []
    await stop(service, task)


@aio
async def test_retry_backs_off_up_to_the_interval(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [fetcher.FetchError('x')]
    service = TickerService(store, fetch=fake, clock=clock)
    a = store.add(TickerSource(url='http://a/rss', interval=600))
    task = await start(service)
    delays = []
    for _ in range(6):
        delays.append(store.state[a.id].next_due - clock.now)
        clock.now = store.state[a.id].next_due
        service.poke()
        await settle()
    assert delays == [60, 120, 240, 480, 600, 600]
    await stop(service, task)


@aio
async def test_fetch_now_edit_and_delete_wake_the_task(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [[it(1)]]
    fake.script['http://b/rss'] = [[it(2)]]
    service = TickerService(store, fetch=fake, clock=clock)
    a = store.add(TickerSource(url='http://a/rss', interval=3600))
    task = await start(service)
    assert fake.calls == ['http://a/rss']
    service.fetch_now(a.id)
    await settle()
    assert fake.calls == ['http://a/rss'] * 2
    b = store.add(TickerSource(url='http://b/rss', interval=3600))
    service.source_added(b)
    await settle()
    assert fake.calls[-1] == 'http://b/rss'
    # a disabled source is not fetched, even by fetch_now(None)
    store.edit(b.id, enabled=False)
    service.source_changed(b.id, refetch=False)
    service.fetch_now()
    await settle()
    assert fake.calls.count('http://b/rss') == 1 and fake.calls.count('http://a/rss') == 3
    # enabling it again fetches it at once
    store.edit(b.id, enabled=True)
    service.source_changed(b.id, refetch=True)
    await settle()
    assert fake.calls.count('http://b/rss') == 2
    store.delete(a.id)
    service.source_deleted(a.id)
    await settle()
    assert not task.done()
    await stop(service, task)


@aio
async def test_disabling_resolves_the_notification(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [fetcher.FetchError('x')]
    service = TickerService(store, fetch=fake, clock=clock)
    a = store.add(TickerSource(url='http://a/rss'))
    task = await start(service)
    clock.now += 60
    service.poke()
    await settle()
    assert len(notifications.snapshot(['admin'])) == 1
    store.edit(a.id, enabled=False)
    service.source_changed(a.id, refetch=False)
    assert notifications.snapshot(['admin']) == []
    await stop(service, task)


@aio
async def test_a_failing_listener_does_not_stop_the_scheduler(store, clock):
    fake = Fake()
    fake.script['http://a/rss'] = [[it(1)], [it(1), it(2)]]
    service = TickerService(store, fetch=fake, clock=clock)

    def boom(_items):
        raise RuntimeError('listener down')
    service.on_items = boom
    store.add(TickerSource(url='http://a/rss', interval=60))
    task = await start(service)
    clock.now += 60
    service.poke()
    await settle()
    assert len(fake.calls) == 2 and not task.done()
    await stop(service, task)


@aio
async def test_with_no_source_the_task_sleeps_forever(store, clock):
    service = TickerService(store, fetch=Fake(), clock=clock)
    task = await start(service)
    await settle(100)
    assert not task.done()
    await stop(service, task)


# ── pushes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def windows(tmp_path, store, monkeypatch):
    w = WindowTickers(WindowLayouts(tmp_path / 'window_layouts.json'))
    monkeypatch.setattr(ticker, '_windows', w)
    monkeypatch.setattr(ticker, '_store', store)
    return w


class Sent(list):
    async def __call__(self, viewer, target, **kw):
        self.append((viewer, target, kw))


def test_summary_travels_only_when_the_template_uses_it():
    items = [{'time': 1, 'source': 's', 'title': 't', 'summary': 'sum', 'link': ''}]
    assert push.items_for(TickerSettings(template='{title}'), items)[0]['summary'] == ''
    assert push.items_for(TickerSettings(template='{title} {summary:.50}'), items)[0]['summary'] == 'sum'


@aio
async def test_a_window_that_appears_gets_settings_and_list_only_if_its_bar_is_on(windows, store):
    a = store.add(TickerSource(url='http://a/rss'))
    store.record_success(a.id, [it(1, 50)], None, None)
    sent = Sent()
    await push.window_appeared(sent, 'v1', 0, 'HDMI-A-1')
    assert sent == []                                    # off: not a single message
    windows.put('v1|HDMI-A-1', TickerSettings(enabled=True, template='{title}'))
    await push.window_appeared(sent, 'v1', 7, 'HDMI-A-1')
    assert [m[1] for m in sent] == ['SetTicker', 'TickerItems']
    assert sent[0][2]['window_id'] == 7 and sent[0][2]['enabled'] is True
    assert sent[1][2]['items'][0]['title'] == 't1'


@aio
async def test_a_new_list_goes_to_the_windows_with_a_bar_only(windows):
    windows.put('v1|A', TickerSettings(enabled=True))
    windows.put('v1|B', TickerSettings(enabled=False))
    webviews = {'v1': {'layout': True, 'windows': [{'window_id': 0, 'screen_name': 'A'}, {'window_id': 1, 'screen_name': 'B'},
                                   {'window_id': 2, 'screen_name': 'C'}]},
                'v2': {'layout': True, 'windows': [{'window_id': 0, 'screen_name': 'A'}]}}
    sent = Sent()
    await push.items_changed(sent, webviews, [{'time': 1, 'source': 's', 'title': 't', 'summary': '', 'link': ''}])
    assert [(v, kw['window_id']) for v, _t, kw in sent] == [('v1', 0)]
    assert sent[0][1] == 'TickerItems'


@aio
async def test_turning_the_bar_off_tells_the_window_without_a_list(windows, store):
    sent = Sent()
    await push.tell_window(sent, 'v1', 3, TickerSettings(enabled=False), [])
    assert [m[1] for m in sent] == ['SetTicker'] and sent[0][2]['enabled'] is False


@aio
async def test_one_unreachable_viewer_does_not_block_the_rest(windows):
    windows.put('v1|A', TickerSettings(enabled=True))
    windows.put('v2|A', TickerSettings(enabled=True))
    webviews = {'v1': {'layout': True, 'windows': [{'window_id': 0, 'screen_name': 'A'}]},
                'v2': {'layout': True, 'windows': [{'window_id': 0, 'screen_name': 'A'}]}}
    got = []

    async def send(viewer, target, **kw):
        if viewer == 'v1':
            raise ConnectionError('gone')
        got.append(viewer)
    await push.items_changed(send, webviews, [])
    assert got == ['v2']


# ── window storage and backup ────────────────────────────────────────────────

def test_the_bar_lives_inside_the_window_layout(tmp_path):
    from utils.models.layout import WindowLayout
    layouts = WindowLayouts(tmp_path / 'l.json')
    w = WindowTickers(layouts)
    w.put('v|A', TickerSettings(enabled=True, rows=2, speed=3))
    assert w.get('v|A').rows == 2 and w.get('v|nothing') == TickerSettings()
    # saved to the layout file, and reloaded with it
    again = WindowTickers(WindowLayouts.load(tmp_path / 'l.json'))
    assert again.get('v|A').speed == 3
    # a layout saved by the panes editor keeps the bar (and a window with only a bar keeps its entry)
    layouts.put('v|A', layouts.get('v|A').model_copy(update={'area': layouts.get('v|A').area}))
    assert w.get('v|A').enabled
    # a default bar on a default layout leaves no entry
    w.put('v|A', TickerSettings())
    assert 'v|A' not in layouts.windows
    assert WindowLayout().ticker == TickerSettings()


def test_a_viewer_without_layout_support_is_not_sent_the_bar(windows):
    windows.put('v1|A', TickerSettings(enabled=True))
    sent = Sent()
    asyncio.new_event_loop().run_until_complete(push.items_changed(sent, {'v1': {'windows': [{'window_id': 0, 'screen_name': 'A'}]}}, []))
    assert sent == []


def test_backup_part_roundtrip_and_reset(store, windows, monkeypatch):
    import sys
    import types
    if 'utils.storage.uploadmanager' not in sys.modules:
        try:
            import utils.storage.uploadmanager      # noqa: F401
        except PermissionError:
            # a machine where /var/lib/unitotem cannot be created (a developer's PC): the uploads are not under test here
            stub = types.ModuleType('utils.storage.uploadmanager')
            stub.upload_manager = types.SimpleNamespace(files=[], remove=lambda f: None)
            monkeypatch.setitem(sys.modules, 'utils.storage.uploadmanager', stub)
    from utils import backup
    part = backup.parts()['ticker']
    assert part.export_files is None
    store.add(TickerSource(url='http://a/rss', name='A'))
    data = json.loads(json.dumps(part.export()))
    assert data['sources'][0]['url'] == 'http://a/rss' and 'windows' not in data     # the bars are part of the layouts
    assert 'items' not in data                         # what the feeds said is cache, not backup
    part.reset()
    assert store.sources == []
    part.restore(data, 1)
    assert [s.name for s in store.sources] == ['A']
    # a hostile archive: bad entries are skipped, not fatal
    part.restore({'sources': [{'url': 'file:///etc/passwd'}, {'url': 'http://ok/rss'}]}, 1)
    assert [s.url for s in store.sources] == ['http://ok/rss']
