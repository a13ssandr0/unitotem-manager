import json
from io import BytesIO
from time import strftime
from typing import Annotated
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from fastapi import APIRouter, Depends, UploadFile, Body, HTTPException
from starlette import status
from starlette.responses import Response

import utils.constants as const
from routers.login import LOGMAN
from utils.models.assets import AssetsManager
from utils.models.playlists import playlists_manager
from utils.models.user import UserPerms, user_manager
from utils.storage.uploadmanager import upload_manager
from utils.system.audio import get_default_audio_device, set_default_audio_device
from utils.system.crontab import CRONTAB
from utils.system.network.netplan import set_netplan, generate_netplan, get_netplan_file, get_netplan_file_list

router = APIRouter()


@router.get("/backup", dependencies=[Depends(LOGMAN)])
async def create_backup(include_uploaded: bool = False):
    CRONTAB.read()
    config_backup = {
        "version": const.__version__,
        "CONFIG": {
            "playlists": {pid: json.loads(am.model_dump_json()) for pid, am in playlists_manager.playlists.items()},
            "playlists_default": playlists_manager._default_id,
            "users": json.loads(user_manager.model_dump_json()),
        },
        "hostname": __import__('socket').gethostname(),
        "def_audio_dev": get_default_audio_device(),
        "netplan": {fname: get_netplan_file(fname) for fname in get_netplan_file_list()},
        "cron": CRONTAB.serialize()
    }

    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w', ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr("config.json", json.dumps(config_backup))
        if include_uploaded:
            for file in upload_manager.files:
                zip_file.write(file.absolute(), "uploaded/" + file.name)
    zip_buffer.seek(0)
    return Response(content=zip_buffer.read(), media_type='application/zip',
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
                config_json = json.loads(zip_file.read('config.json'))

                from packaging.version import Version
                bkp_ver = Version(config_json.get('version', '0'))

                if 'CONFIG' in config_json and CONFIG:
                    cfg = config_json['CONFIG']
                    if isinstance(cfg, str):
                        cfg = json.loads(cfg)
                    if 'users' in cfg:
                        user_manager.__init__(**cfg['users'])
                        user_manager.save()
                    if 'playlists' in cfg:
                        playlists_manager._playlists.clear()
                        for pid, am_data in cfg['playlists'].items():
                            am = AssetsManager.model_validate(am_data)
                            am._filepath = playlists_manager._playlist_path(am.playlist_id)
                            am.save()
                            playlists_manager._playlists[pid] = am
                        playlists_manager._default_id = cfg.get('playlists_default')
                        playlists_manager._save_index()

                if 'hostname' in config_json and hostname:
                    from utils.system.dbus_system import set_static_hostname
                    await set_static_hostname(config_json['hostname'])

                if 'def_audio_dev' in config_json and def_audio_dev:
                    if bkp_ver >= Version('3.0.0'):
                        # with version 3.0.0 audio controls changed from alsa to pulseaudio
                        set_default_audio_device(config_json['def_audio_dev'])

                if 'netplan' in config_json and netplan:
                    set_netplan(filename=None, file_content=config_json['netplan'], apply=False)
                    res = generate_netplan()
                    if isinstance(res, str):
                        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=res)

            if uploaded:
                for filename in files:
                    if filename.startswith('uploaded/'):
                        with zip_file.open(filename) as infile:
                            _ = await upload_manager.save(infile, filename)

    except BadZipFile as e:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail=str(e))


@router.delete('/backup', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(LOGMAN)])
async def factory_reset():
    # Reset users to default admin
    user_manager.root.clear()
    user_manager.add_user('admin', 'admin', {UserPerms.admin})

    # Clear all playlist assets
    for am in playlists_manager.playlists.values():
        am.assets.clear()
        am.save()

    CRONTAB.read()
    CRONTAB.remove_all(comment=CRONTAB._cron_re)
    CRONTAB.write()

    for file in list(upload_manager.files):
        upload_manager.remove(file)
