from typing import Optional, Union

from pydantic import FutureDatetime
from pydantic_extra_types.color import Color

from api.ws.endpoints import WSAPIBase
from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSManager
from utils.models.assets import FitEnum, MediaType, assets_manager
from utils.models.user import UserPerms
from utils.storage.uploadmanager import upload_manager


class Scheduler(WSAPIBase):
    def __init__(self, ws: WSManager, remote_ws: WSManager):
        super().__init__(ws, remote_ws)
        assets_manager.set_on_assets_update(
            lambda assets, current: ws.broadcast('Scheduler/asset', items=assets, current=current))
        assets_manager.set_on_current_update(lambda current: ws.broadcast('Scheduler/current', current=current))

    @UserPerms.requires.scheduler
    def asset(self):
        return WSBroadcast(items=assets_manager.serialize_assets(), current=assets_manager.current.uuid)

    @UserPerms.requires.scheduler
    def file(self):
        return WSBroadcast(files=upload_manager.serialize(), disk_used=upload_manager.disk_usedh,
                           disk_total=upload_manager.disk_totalh)

    @UserPerms.requires.scheduler
    def add_url(self, items: list):
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            element.pop('uuid', None)  # uuid MUST be generated internally
            assets_manager.append(element)
        assets_manager.save()

    @UserPerms.requires.scheduler
    def add_file(self, items: list):
        invalid = []
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            if element['url'] in upload_manager.filenames:  # type: ignore
                assets_manager.append({
                    'url'       : 'file:' + element['url'],
                    'name'      : element['url'],
                    'duration'  : element.get('duration', upload_manager.files_info[element['url']].duration_s),
                    'enabled'   : element.get('enabled', False),
                    'media_type': element.get('media_type', upload_manager.files_info[element['url']].mime)
                })
            else:
                invalid.append(element)
        assets_manager.save()
        if invalid:
            return WSResponse(error='Invalid elements', extra=invalid)
        return None

    # noinspection PyTypeHints
    @UserPerms.requires.scheduler
    def edit(self,
             uuid: str,
             name: Optional[str] = None,
             url: Optional[str] = None,
             duration: Optional[Union[int, float]] = None,
             fit: Optional[FitEnum] = None,
             bg_color: Optional[Color] = None,
             ena_date: Optional[FutureDatetime] = None,
             dis_date: Optional[FutureDatetime] = None,
             enabled: Optional[bool] = None):
        asset = assets_manager[uuid]

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
        assets_manager.save()

    @UserPerms.requires.scheduler
    def current(self):
        if assets_manager.count_enabled():
            return WSBroadcast(current=assets_manager.current.model_dump(mode='json'))
        return None

    @UserPerms.requires.scheduler
    def delete(self, uuid: str):
        del assets_manager[uuid]
        assets_manager.save()

    @UserPerms.requires.scheduler
    def delete_file(self, files: list[str]):
        for file in files:
            upload_manager.remove(file)
        assets_manager.save()

    @UserPerms.requires.scheduler
    def goto(self, index: Union[None, int, str] = None):
        assets_manager.goto_a(index)

    @UserPerms.requires.scheduler
    def back(self):
        assets_manager.prev_a()

    @UserPerms.requires.scheduler
    def next(self):
        assets_manager.next_a()

    @UserPerms.requires.scheduler
    def reorder(self, from_i: int, to_i: int):
        assets_manager.move(from_i, to_i)
        assets_manager.save()


class Settings(WSAPIBase):
    @UserPerms.requires.scheduler
    def default_duration(self, duration: Optional[int] = None):
        if duration is not None:
            assets_manager.default_duration = duration
            assets_manager.save()
        return WSBroadcast(duration=assets_manager.default_duration)
