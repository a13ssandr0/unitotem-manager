import json
from io import BytesIO
from time import strftime
from typing import Annotated, Optional
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from starlette import status
from starlette.responses import Response

import utils.constants as const
from routers.login import require_perm
from utils import backup

router = APIRouter()


def _select(keys: Optional[list[str]], *, default) -> list:
    """The parts asked for, in the registry's order; unknown names are an error, not silently ignored."""
    available = backup.parts()
    if not keys:
        return [p for p in available.values() if default(p)]
    unknown = [k for k in keys if k not in available]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f'Unknown parts: {unknown}')
    return [available[k] for k in keys]


@router.get('/backup/parts', dependencies=[Depends(require_perm('admin'))])
async def backup_parts():
    """What this install can back up, restore and reset (the web UI builds its checkboxes from this)."""
    return backup.describe()


@router.get('/backup', dependencies=[Depends(require_perm('admin'))])
async def create_backup(parts: Annotated[Optional[list[str]], Query()] = None):
    selected = _select(parts, default=lambda p: not p.optional_in_backup)
    data = {key: part.export() for part in selected if part.export for key in [part.key]}

    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w', ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr('config.json', json.dumps({'version': const.__version__, 'parts': data}))
        for part in selected:
            if part.export_files:
                await backup.maybe_await(part.export_files(zip_file))
    zip_buffer.seek(0)
    return Response(content=zip_buffer.read(), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="' + strftime(
                        'unitotem-manager-%Y%m%d-%H%M%S.zip') + '"'})


@router.post('/backup', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_perm('admin'))])
async def load_backup(backup_file: UploadFile, parts: Annotated[list[str], Form()] = []):
    selected = _select(parts, default=lambda p: False) if parts else []
    try:
        with ZipFile(backup_file.file) as zip_file:
            if 'config.json' not in zip_file.namelist():
                raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail='Not a UniTotem backup')
            config = json.loads(zip_file.read('config.json'))
            data = backup.from_legacy(config)
            version = str(config.get('version', '0'))
            for part in selected:
                try:
                    if part.restore and part.key in data:
                        await backup.maybe_await(part.restore(data[part.key], version))
                    if part.restore_files:
                        await backup.maybe_await(part.restore_files(zip_file))
                except ValueError as exc:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                        detail=f'{part.label}: {exc}')
    except BadZipFile as e:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail=str(e))


@router.delete('/backup', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_perm('admin'))])
async def factory_reset(parts: Annotated[Optional[list[str]], Query()] = None):
    for part in _select(parts, default=lambda p: p.reset is not None):
        if part.reset:
            await backup.maybe_await(part.reset())
