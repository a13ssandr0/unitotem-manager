from typing import Optional, Union

import requests
from loguru import logger

from utils import constants as const

from pydantic import FutureDatetime
from pydantic_extra_types.color import Color

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSManager
from utils.models.assets import AssetsManager, FitEnum, MediaType
from utils.models.playlists import playlists_manager
from utils.models.user import UserPerms
from utils.storage.uploadmanager import upload_manager


def _make_asset_broadcast(am: AssetsManager) -> WSBroadcast:
    return WSBroadcast(
        playlist_id=am.playlist_id,
        items=am.serialize_assets(),
        current=am.current.uuid
    )


def probe_media_type(url: str) -> MediaType:
    """
    Ask the server what a URL actually serves, so an address pointing straight
    at a picture or a video is shown as one instead of in an iframe.

    This probe belongs here rather than in the viewer page, even though the
    pre-Qt implementation did it in the browser: that page is loaded over
    file://, so a cross-origin HEAD is refused before it is even sent and every
    URL would come back looking like a web page. The manager has no such limit.

    Returns MediaType.undefined when the answer cannot be obtained, which the
    viewer treats as "decide for yourself" and ultimately renders as a page -
    the same outcome the old implementation had on error.
    """
    if not url.lower().startswith(('http://', 'https://')):
        return MediaType.undefined
    # Identify ourselves: several large sites (Wikimedia among them) answer a
    # default python-requests User-Agent with 403 and an HTML or text/plain
    # error body, which is exactly the kind of answer that must not be mistaken
    # for the asset's own type.
    headers = {'User-Agent': f'UniTotem/{const.__version__} (https://github.com/a13ssandr0/unitotem-manager)'}
    mime = ''
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True,
                             verify=False, headers=headers)
        # Plenty of servers refuse HEAD, or refuse an unfamiliar client
        # outright (Wikimedia answers 400 unless the User-Agent carries a
        # contact address). Any unsuccessful answer is retried as a one-byte
        # ranged GET, which is cheap and is what such servers do accept.
        if not resp.ok:
            resp = requests.get(url, timeout=5, allow_redirects=True, verify=False,
                                headers={**headers, 'Range': 'bytes=0-0'}, stream=True)
            resp.close()
        if resp.ok:
            mime = resp.headers.get('Content-Type', '')
        else:
            logger.warning('Media type probe for {} returned HTTP {}', url, resp.status_code)
    except requests.RequestException as exc:
        logger.warning('Cannot determine the media type of {}: {}', url, exc)
        return MediaType.undefined
    for kind in ('image', 'video', 'audio'):
        if kind in mime:
            return MediaType[kind]
    # An empty mime means the probe gave no usable answer: leave it undefined
    # so the viewer decides, instead of asserting it is a web page.
    return MediaType.web if mime else MediaType.undefined


class Scheduler(WSAPIBase):
    def __init__(self, ws: WSManager, remote_ws: WSManager):
        super().__init__(ws, remote_ws)
        # Register callbacks for all existing playlists
        for am in playlists_manager.playlists.values():
            self._bind_playlist_callbacks(am)

    def _bind_playlist_callbacks(self, am: AssetsManager):
        pid = am.playlist_id
        _ws = self.ws
        am.set_on_assets_update(
            lambda assets, current, _pid=pid: _ws.broadcast(
                'Scheduler/asset', playlist_id=_pid, items=assets, current=current
            )
        )
        am.set_on_current_update(
            lambda current, _pid=pid: _ws.broadcast(
                'Scheduler/current', playlist_id=_pid, current=current
            )
        )

    @UserPerms.requires.scheduler
    def playlists(self):
        return WSBroadcast(playlists=playlists_manager.serialize())

    @UserPerms.requires.scheduler
    def create_playlist(self, name: str = 'New Playlist'):
        am = playlists_manager.create(name)
        self._bind_playlist_callbacks(am)
        # Also start a loop for the new playlist in WebviewManager
        try:
            from webview.controller import WebviewManager
            WebviewManager.get_instance().add_playlist_loop(am)
        except RuntimeError:
            pass
        return WSBroadcast(playlists=playlists_manager.serialize())

    @UserPerms.requires.scheduler
    def delete_playlist(self, playlist_id: str):
        try:
            playlists_manager.delete(playlist_id)
            try:
                from webview.controller import WebviewManager
                WebviewManager.get_instance().remove_playlist_loop(playlist_id)
            except RuntimeError:
                pass
        except ValueError as e:
            return WSResponse(error=str(e))
        return WSBroadcast(playlists=playlists_manager.serialize())

    @UserPerms.requires.scheduler
    def asset(self, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        return _make_asset_broadcast(am)

    @UserPerms.requires.scheduler
    def file(self):
        return WSBroadcast(files=upload_manager.serialize(), disk_used=upload_manager.disk_usedh,
                           disk_total=upload_manager.disk_totalh)

    @UserPerms.requires.scheduler
    def add_url(self, items: list, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            element.pop('uuid', None)
            if 'media_type' not in element:
                element['media_type'] = probe_media_type(element['url'])
            am.append(element)
        am.save()

    @UserPerms.requires.scheduler
    def add_file(self, items: list, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        invalid = []
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            if element['url'] in upload_manager.filenames:  # type: ignore
                am.append({
                    'url'       : 'file:' + element['url'],
                    'name'      : element['url'],
                    'duration'  : element.get('duration', upload_manager.files_info[element['url']].duration_s),
                    'enabled'   : element.get('enabled', False),
                    'media_type': element.get('media_type', upload_manager.files_info[element['url']].mime)
                })
            else:
                invalid.append(element)
        am.save()
        if invalid:
            return WSResponse(error='Invalid elements', extra=invalid)
        return None

    # noinspection PyTypeHints
    @UserPerms.requires.scheduler
    def edit(self,
             uuid: str,
             playlist_id: Optional[str] = None,
             name: Optional[str] = None,
             url: Optional[str] = None,
             duration: Optional[Union[int, float]] = None,
             fit: Optional[FitEnum] = None,
             bg_color: Optional[Color] = None,
             ena_date: Optional[FutureDatetime] = None,
             dis_date: Optional[FutureDatetime] = None,
             enabled: Optional[bool] = None):
        am = playlists_manager.get(playlist_id)
        asset = am[uuid]

        if name is not None and asset.name != name:
            asset.name = name
        if url is not None and asset.url != url:
            asset.url = url
            # Re-derive from the upload manager when the file is known;
            # otherwise leave it undefined on purpose, which tells the viewer
            # to probe the URL and choose the container itself.
            filename = url.removeprefix('file:')
            info = upload_manager.files_info.get(filename) if url.startswith('file:') else None
            asset.media_type = info.mime if info is not None else probe_media_type(url)
        if duration is not None and asset.duration != duration:
            asset.update_duration(duration)
        if fit is not None and asset.fit != fit:
            asset.fit = fit
        if bg_color is not None and asset.bg_color != bg_color:
            asset.bg_color = bg_color
        if ena_date is not None and asset.ena_date != ena_date:
            asset.ena_date = ena_date
        if dis_date is not None and asset.dis_date != dis_date:
            asset.dis_date = dis_date
        if enabled is not None and asset.enabled != enabled:
            asset.enabled = enabled
        am.save()

    @UserPerms.requires.scheduler
    def current(self, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        if am.count_enabled():
            return WSBroadcast(playlist_id=am.playlist_id,
                               current=am.current.model_dump(mode='json'))
        return None

    @UserPerms.requires.scheduler
    def delete(self, uuid: str, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        del am[uuid]
        am.save()

    @UserPerms.requires.scheduler
    def delete_file(self, files: list[str]):
        for file in files:
            upload_manager.remove(file)
        playlists_manager.default.save()

    @UserPerms.requires.scheduler
    def goto(self, index: Union[None, int, str] = None, playlist_id: Optional[str] = None):
        playlists_manager.get(playlist_id).goto_a(index)

    @UserPerms.requires.scheduler
    def back(self, playlist_id: Optional[str] = None):
        playlists_manager.get(playlist_id).prev_a()

    @UserPerms.requires.scheduler
    def next(self, playlist_id: Optional[str] = None):
        playlists_manager.get(playlist_id).next_a()

    @UserPerms.requires.scheduler
    def reorder(self, from_i: int, to_i: int, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        am.move(from_i, to_i)
        am.save()


class Settings(WSAPIBase):
    @UserPerms.requires.scheduler
    def default_duration(self, duration: Optional[int] = None, playlist_id: Optional[str] = None):
        am = playlists_manager.get(playlist_id)
        if duration is not None:
            am.default_duration = duration
            am.save()
        return WSBroadcast(duration=am.default_duration)
