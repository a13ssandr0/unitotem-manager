from io import BytesIO
from json import dumps, loads
from platform import node as get_hostname
from time import strftime
from typing import Annotated
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from fastapi import APIRouter, Depends, UploadFile, Body, HTTPException
from starlette import status
from starlette.responses import Response

import utils.constants as const
from utils.audio import getDefaultAudioDevice, setDefaultAudioDevice
from utils.commons import UPLOADS
from utils.models import Config
from utils.network import get_netplan_file_list, get_netplan_file, set_netplan, set_hostname, generate_netplan
from utils.security import LOGMAN
from utils.system import CRONTAB

router = APIRouter()


@router.get("/backup", dependencies=[Depends(LOGMAN)])
async def create_backup(include_uploaded: bool = False):
    CRONTAB.read()
    config_backup = {
        "version": const.__version__,
        "CONFIG": Config.model_dump_json(),
        "hostname": get_hostname(),
        "def_audio_dev": getDefaultAudioDevice(),
        "netplan": {fname: get_netplan_file(fname) for fname in get_netplan_file_list()},
        "cron": CRONTAB.serialize()
    }

    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w', ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr("config.json", dumps(config_backup))
        if include_uploaded:
            for file in UPLOADS.files:
                zip_file.write(file.absolute(), "uploaded/" + file.name)
    zip_buffer.seek(0)
    return Response(content=zip_buffer, media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="' + strftime(
                        'unitotem-manager-%Y%m%d-%H%M%S.zip') + '"'})


# noinspection PyPep8Naming
@router.post("/backup", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(LOGMAN)])
async def load_backup(backup_file: UploadFile,
                      CONFIG: Annotated[bool, Body()] = False,
                      def_audio_dev: Annotated[bool, Body()] = False,
                      hostname: Annotated[bool, Body()] = False,
                      netplan: Annotated[bool, Body()] = False,
                      uploaded: Annotated[bool, Body()] = False):
    try:
        with ZipFile(backup_file.file) as zip_file:
            files = zip_file.namelist()
            if 'config.json' in files:
                config_json = loads(zip_file.read('config.json'))

                from packaging.version import Version
                bkp_ver = Version(config_json.get('version', '0'))

                if 'CONFIG' in config_json and CONFIG:
                    Config(obj=config_json['CONFIG'])
                    Config.save()

                if 'hostname' in config_json and hostname:
                    set_hostname(config_json['hostname'])

                if 'def_audio_dev' in config_json and def_audio_dev:
                    if bkp_ver >= Version('3.0.0'):
                        # with version 3.0.0 audio controls changed from alsa to pulseaudio
                        setDefaultAudioDevice(config_json['def_audio_dev'])

                if 'netplan' in config_json and netplan:
                    set_netplan(filename=None, file_content=config_json['netplan'], apply=False)

            if uploaded:
                for filename in files:
                    if filename.startswith('uploaded/'):
                        with zip_file.open(filename) as infile:
                            filename = await UPLOADS.save(infile, filename)

            res = generate_netplan()
            if isinstance(res, str):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=res)

    except BadZipFile as e:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail=str(e))


@router.delete('/backup', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(LOGMAN)])
async def factory_reset():
    Config.reset()

    CRONTAB.read()
    # noinspection PyProtectedMember
    CRONTAB.remove_all(comment=CRONTAB._cron_re)
    CRONTAB.write()

    await const.UI_WS.broadcast('reset', nocache=True)

    for file in UPLOADS.files:
        UPLOADS.remove(file)
