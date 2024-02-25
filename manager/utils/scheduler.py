from datetime import datetime
from typing import Optional, Union, Annotated, Literal

from fastapi import WebSocket
from pydantic import BeforeValidator
from pydantic.color import Color

from utils.commons import UPLOADS
from utils.models import Config, FitEnum, validate_date, MediaType
from utils.ws.endpoints import WSAPIBase


class Scheduler(WSAPIBase):
    async def asset(self):
        await self.ws.broadcast('scheduler/asset', items=Config.assets.serialize(), current=Config.assets.current.uuid)

    async def file(self):
        await self.ws.broadcast('scheduler/file', files=UPLOADS.serialize())

    def add_url(self, items: list[str | dict] = []):
        for element in items:
            if isinstance(element, str):
                element = {'url': element}
            element.pop('uuid', None)  # uuid MUST be generated internally
            Config.assets.append(element)
        Config.save()

    async def add_file(self, ws: WebSocket, items: list[str | dict] = []):
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
            await self.ws.send(ws, 'scheduler/add_file', error='Invalid elements', extra=invalid)

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

    async def current(self):
        if Config.enabled_asset_count:
            await self.ws.broadcast('scheduler/current', uuid=Config.assets.current.uuid)

    def delete(self, uuid: str):
        del Config.assets[uuid]
        Config.save()

    def delete_file(self, files: list[str]):
        for file in files:
            UPLOADS.remove(file)
        Config.save()

    def goto(self, index: Union[None, int, str] = None):
        Config.assets.goto_a(index)

    class Goto(WSAPIBase):
        def back(self):
            Config.assets.prev_a()

        def next(self):
            Config.assets.next_a()

    def reorder(self, from_i: int, to_i: int):
        Config.assets.move(from_i, to_i)
        Config.save()
