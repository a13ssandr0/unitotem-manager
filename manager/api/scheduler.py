from typing import Optional, Union

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
        # Also start a loop for the new playlist in ViewerManager
        try:
            from utils.viewer_manager import ViewerManager
            ViewerManager.get_instance().add_playlist_loop(am)
        except RuntimeError:
            pass
        return WSBroadcast(playlists=playlists_manager.serialize())

    @UserPerms.requires.scheduler
    def delete_playlist(self, playlist_id: str):
        try:
            playlists_manager.delete(playlist_id)
            try:
                from utils.viewer_manager import ViewerManager
                ViewerManager.get_instance().remove_playlist_loop(playlist_id)
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
            asset.media_type = MediaType.undefined
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
