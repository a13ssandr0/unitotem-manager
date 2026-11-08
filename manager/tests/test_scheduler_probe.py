"""The media type probe of the scheduler: TLS verified, failures are undefined, and edit_asset leaves the event loop free."""
import asyncio
import threading
from types import SimpleNamespace

import requests

from api.ws.endpoints import WS  # noqa: F401  (the endpoints module must load first: api.scheduler imports it)
import api.scheduler as scheduler_module
from api.scheduler import Scheduler, probe_media_type
from utils.models.assets import MediaType


class Response:
    ok = True
    status_code = 200
    headers = {'Content-Type': 'image/png'}

    def close(self):
        pass


def test_the_probe_verifies_tls(monkeypatch):
    seen = {}

    def head(url, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(scheduler_module.requests, 'head', head)
    assert probe_media_type('https://example.test/a.png') is MediaType.image
    assert 'verify' not in seen


def test_a_certificate_error_gives_undefined_not_an_exception(monkeypatch):
    def head(url, **kwargs):
        raise requests.exceptions.SSLError('certificate verify failed')

    monkeypatch.setattr(scheduler_module.requests, 'head', head)
    assert probe_media_type('https://example.test/a.png') is MediaType.undefined


def _asset():
    return SimpleNamespace(name='a', url='http://old.test/x', media_type=MediaType.undefined, duration=10, fit=None,
                           bg_color=None, ena_date=None, dis_date=None, volume=1.0, muted=False, zoom=1.0,
                           allow_pinch_zoom=False, enabled=True)


class _Playlist:
    def __init__(self, asset):
        self._asset = asset
        self.saved = False

    def __getitem__(self, uuid):
        return self._asset

    def revalidate_current(self):
        pass

    def save(self):
        self.saved = True


def test_edit_asset_probes_in_a_thread_and_the_loop_keeps_running(monkeypatch):
    asset = _asset()
    playlist = _Playlist(asset)
    monkeypatch.setattr(scheduler_module, 'playlists_manager', SimpleNamespace(get=lambda _id: playlist))
    probe_threads = []

    def slow_probe(url):
        probe_threads.append(threading.current_thread())
        threading.Event().wait(0.3)             # a blocking network call
        return MediaType.video

    monkeypatch.setattr(scheduler_module, 'probe_media_type', slow_probe)

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.02)

        t = asyncio.create_task(ticker())
        await Scheduler.edit_asset(SimpleNamespace(), 'uuid-1', url='http://new.test/clip.mp4')
        t.cancel()
        return ticks

    ticks = asyncio.run(main())
    assert asset.url == 'http://new.test/clip.mp4'
    assert asset.media_type is MediaType.video
    assert playlist.saved
    assert probe_threads and probe_threads[0] is not threading.main_thread()
    assert ticks >= 5                           # the loop ran while the probe blocked
