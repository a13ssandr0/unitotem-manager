"""The announcer and the browser are tried again when they fail, and a stop cancels the pending attempt.

The zeroconf classes are replaced: nothing is sent on the network."""
import asyncio

import utils.remote.discovery as discovery


class FakeZeroconf:
    registered: list = []
    closed = 0

    def __init__(self, ip_version=None):
        self.zeroconf = object()

    async def async_register_service(self, info):
        FakeZeroconf.registered.append(info)

    async def async_unregister_service(self, info):
        pass

    async def async_close(self):
        FakeZeroconf.closed += 1


class FakeBrowser:
    fail_next = 0
    created = 0

    def __init__(self, zc, service_type, handlers):
        if FakeBrowser.fail_next:
            FakeBrowser.fail_next -= 1
            raise OSError('address in use')
        FakeBrowser.created += 1

    async def async_cancel(self):
        pass


def _fresh(monkeypatch):
    FakeZeroconf.registered = []
    FakeZeroconf.closed = 0
    FakeBrowser.fail_next = 0
    FakeBrowser.created = 0
    monkeypatch.setattr(discovery, 'AVAILABLE', True)
    monkeypatch.setattr(discovery, 'AsyncZeroconf', FakeZeroconf)
    monkeypatch.setattr(discovery, 'AsyncServiceBrowser', FakeBrowser)
    monkeypatch.setattr(discovery, 'RETRY_DELAYS', (0.02, 0.02, 0.02))


async def _wait(predicate, seconds=3.0):
    for _ in range(int(seconds / 0.02)):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_an_announcement_without_an_address_is_made_once_one_appears(monkeypatch):
    _fresh(monkeypatch)
    addresses = []
    monkeypatch.setattr(discovery, 'local_addresses', lambda: list(addresses))

    async def main():
        ann = discovery.Announcer()
        await ann.start('a' * 32, 'lobby', 443, 4443)
        assert ann.active is False and ann._retry is not None
        addresses.append('10.0.0.7')                 # the network comes up
        assert await _wait(lambda: ann.active)
        assert len(FakeZeroconf.registered) == 1
        await ann.stop()
        assert ann.active is False and ann._retry is None

    asyncio.run(main())


def test_a_failed_registration_is_retried(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(discovery, 'local_addresses', lambda: ['10.0.0.7'])
    attempts = []

    class Flaky(FakeZeroconf):
        async def async_register_service(self, info):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('busy')
            await super().async_register_service(info)

    monkeypatch.setattr(discovery, 'AsyncZeroconf', Flaky)

    async def main():
        ann = discovery.Announcer()
        await ann.start('b' * 32, 'lobby', 443, 4443)
        assert await _wait(lambda: ann.active)
        assert len(attempts) == 2
        await ann.stop()

    asyncio.run(main())


def test_stop_cancels_a_pending_announcement_retry(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(discovery, 'RETRY_DELAYS', (60,))
    monkeypatch.setattr(discovery, 'local_addresses', lambda: [])

    async def main():
        ann = discovery.Announcer()
        await ann.start('c' * 32, 'lobby', 443, 4443)
        retry = ann._retry
        await ann.stop()
        await asyncio.sleep(0)
        assert retry.cancelled()
        assert FakeZeroconf.registered == []

    asyncio.run(main())


def test_a_browser_that_cannot_open_is_opened_on_a_later_attempt(monkeypatch):
    _fresh(monkeypatch)
    FakeBrowser.fail_next = 1

    async def main():
        browser = discovery.Browser(lambda: None)
        await browser.start()
        assert browser.active is False
        assert await _wait(lambda: browser.active)
        assert FakeBrowser.created == 1
        await browser.stop()
        assert browser.active is False

    asyncio.run(main())
