from datetime import datetime
from typing import Optional, Union, Annotated, Literal

from pydantic import BeforeValidator
from pydantic.color import Color

from utils.commons import UPLOADS
from utils.models import Config, FitEnum, validate_date, MediaType, UserPerms
from utils.ws.endpoints import WSAPIBase
from utils.ws.responses import WSBroadcast, WSResponse


class Scheduler(WSAPIBase):
    @UserPerms.requires.scheduler
    def asset(self):
        return WSBroadcast(self.asset, items=Config.assets.serialize(), current=Config.assets.current.uuid)

    @UserPerms.requires.scheduler
    def file(self):
        return WSBroadcast(self.file, files=UPLOADS.serialize())

    @UserPerms.requires.scheduler
    def add_url(self, items: list[str | dict] = []):
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            element.pop('uuid', None)  # uuid MUST be generated internally
            Config.assets.append(element)
        Config.save()

    @UserPerms.requires.scheduler
    def add_file(self, items: list[str | dict] = []):
        invalid = []
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            if element['url'] in UPLOADS.filenames:  # type: ignore
                Config.assets.append({
                    'url': 'file:' + element['url'],
                    'name': element['url'],
                    'duration': element.get('duration', UPLOADS.files_info[element['url']].duration_s),
                    'enabled': element.get('enabled', False),
                    'media_type': element.get('media_type', UPLOADS.files_info[element['url']].mime)
                })
            else:
                invalid.append(element)
        Config.save()
        if invalid:
            return WSResponse(self.add_file, error='Invalid elements', extra=invalid)

    @UserPerms.requires.scheduler
    def edit(self,
                   uuid: str,
                   name: Optional[str] = None,
                   url: Optional[str] = None,
                   duration: Optional[Union[int, float]] = None,
                   fit: Optional[FitEnum] = None,
                   bg_color: Union[Color, None, Literal[-1]] = -1,
                   ena_date: Annotated[Optional[datetime], BeforeValidator(validate_date)] = None,
                   dis_date: Annotated[Optional[datetime], BeforeValidator(validate_date)] = None,
                   enabled: Optional[bool] = None):
        asset = Config.assets[uuid]
        if name is not None and asset.name != name:
            asset.name = name
        if url is not None and asset.url != url:
            asset.url = url
            asset.media_type = MediaType.undefined
        if duration is not None and asset.duration != duration:
            asset.duration = duration
        if fit is not None and asset.fit != fit:
            asset.fit = fit
        if bg_color != -1 and asset.bg_color != bg_color:
            asset.bg_color = bg_color
        if ena_date is not None and asset.ena_date != ena_date:
            asset.ena_date = ena_date
        if dis_date is not None and asset.dis_date != dis_date:
            asset.dis_date = dis_date
        if enabled is not None and asset.enabled != enabled:
            if enabled:
                asset.enable()
            else:
                asset.disable()
        Config.save()

    @UserPerms.requires.scheduler
    def current(self):
        if Config.enabled_asset_count:
            return WSBroadcast(self.current, uuid=Config.assets.current.uuid)

    @UserPerms.requires.scheduler
    def delete(self, uuid: str):
        del Config.assets[uuid]
        Config.save()

    @UserPerms.requires.scheduler
    def delete_file(self, files: list[str]):
        for file in files:
            UPLOADS.remove(file)
        Config.save()

    @UserPerms.requires.scheduler
    def goto(self, index: Union[None, int, str] = None):
        Config.assets.goto_a(index)

    @UserPerms.requires.scheduler
    def back(self):
        Config.assets.prev_a()

    @UserPerms.requires.scheduler
    def next(self):
        Config.assets.next_a()

    @UserPerms.requires.scheduler
    def reorder(self, from_i: int, to_i: int):
        Config.assets.move(from_i, to_i)
        Config.save()
