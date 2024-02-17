import asyncio
import base64
import signal
import warnings
from argparse import ArgumentParser
from datetime import datetime
from io import BytesIO
from ipaddress import IPv4Address
from json import dumps, loads
from os import environ
from os.path import exists
from platform import freedesktop_os_release as os_release
from platform import node as get_hostname
from subprocess import run as cmd_run
from time import strftime
from traceback import format_exc, print_exc
from typing import Annotated, Any, Literal, Optional, Union, cast
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import asyncwebsockets
import requests
import urllib3
import uvloop
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import (Body, Depends, FastAPI, HTTPException, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect,
                     WebSocketException, status)
from fastapi.middleware import Middleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import Mount
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from jwt import InvalidSignatureError
from natsort import natsorted
from psutil import (cpu_count, sensors_battery, sensors_fans,
                    sensors_temperatures, virtual_memory)
from pydantic import BeforeValidator, PositiveInt
from pydantic_extra_types.color import Color
from watchdog.observers import Observer
from werkzeug.utils import secure_filename
from wsproto.events import CloseConnection

from utils import *

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)

# APT_THREAD           = Thread(target=apt_update, name='update')

DEF_WIFI_CARD = get_ifaces(IF_WIRELESS)[0]
DEFAULT_AP = None

DISPLAYS: list[dict] = []
WINDOW = {'bounds': {}, 'orientation': -2, 'flip': -2}

SHUTDOWN_EVENT = asyncio.Event()

REMOTE_WS = WSManager(True)
UI_WS = WSManager(True)
WS = WSManager()

REMOTE_CONNECTED = False

TEMPLATES = Jinja2Templates(const.templates_folder, extensions=['jinja2.ext.do'])
TEMPLATES.env.filters['flatten'] = flatten
UPLOADS = UploadManager(const.uploads_folder, lambda x: WS.broadcast('scheduler/file', files=x))

WWW = FastAPI(
    title='UniTotem', version=const.__version__,
    middleware=[Middleware(HTTPSRedirectMiddleware)],  # type: ignore
    routes=[
        Mount('/static', StaticFiles(directory=const.static_folder), name='static'),
        Mount('/uploaded', StaticFiles(directory=const.uploads_folder), name='uploaded')
    ],
    exception_handlers={
        InvalidSignatureError: login_redir,
        NotAuthenticatedException: login_redir
    }
)
WWW.include_router(login_router)


@WWW.websocket("/remote")
async def remote_websocket(websocket: WebSocket):
    if Config.remote_server_ip:
        # immediately refuse connections if remote_server is configured (!=None)
        # this means that this instance is running in client/slave mode
        # and someone is trying either to connect from another client or
        # +----------------------+ is trying to be funny and
        # |                      | discover what happens
        # |    OOOOOOOOO         | when the snake eats itself!
        # |    O  *    O         |
        # |    O  X    O         |
        # |    OOOO    O         |
        # |            O         |
        # |            O         |
        # +----------------------+
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    if not websocket.client or 'instance_id' not in websocket.headers:
        return

    await REMOTE_WS.connect(websocket)

    Config.remote_clients[websocket.headers['instance_id']] = {
        'ip': websocket.client.host,
        'port': websocket.headers.get('port', const.default_port_secure),
        'hostname': websocket.headers.get('hostname', websocket.headers['instance_id'])
    }
    Config.save()

    while True:
        # noinspection PyBroadException
        try:
            data = await websocket.receive_text()
            print(data)
        except WebSocketDisconnect:
            REMOTE_WS.disconnect(websocket)
            break
        except Exception:
            print_exc()


@WWW.get("/remote/public_key")
async def get_public_key():
    return Response(Config.rsa_pk.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ), media_type="text/plain")


@WWW.websocket("/ui_ws")
async def ui_websocket(websocket: WebSocket):
    global DISPLAYS, WINDOW
    if websocket.scope['client'][0] != websocket.scope['server'][0]:
        # prohibit external connections
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    await UI_WS.connect(websocket)
    while True:
        try:
            data = await websocket.receive_json()
            match data['target']:
                case 'getAllDisplays':
                    DISPLAYS = data['displays']
                case 'getBounds':
                    WINDOW['bounds'] = data['bounds']
                    await WS.broadcast('settings/display/getBounds', **WINDOW['bounds'])
                case 'getOrientation':
                    WINDOW['orientation'] = data['orientation']
                    await WS.broadcast('settings/display/getOrientation', orientation=WINDOW['orientation'])
                case 'getFlip':
                    WINDOW['flip'] = data['flip']
                    await WS.broadcast('settings/display/getFlip', flip=WINDOW['flip'])
                case 'getAllowInsecureCerts':
                    await WS.broadcast('settings/display/allowInsecureCerts', bounds=data['allow'])
                case 'setContainer':
                    try:
                        Config.assets.current.media_type = data['media_type']
                    except IndexError:
                        # no-assets and first-boot pages have an invalid index
                        pass
        except WebSocketDisconnect:
            UI_WS.disconnect(websocket)
            break


@WWW.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    request = Request({'type': 'http'})
    request._cookies = websocket.cookies
    try:
        await LOGMAN(request)
    except NotAuthenticatedException:
        await websocket.accept()
        await websocket.close(1008, 'Not Authenticated')
        return

    await WS.connect(websocket)
    await WS.send(websocket, 'connected')
    while True:
        # noinspection PyBroadException
        try:
            data: dict[str, Any] = await websocket.receive_json()
            t = data.pop('target')
            try:
                await WS.handlers[t](websocket, **data)
            except KeyError:
                await WS.send(websocket, 'error', error='Invalid command', extra=dumps({'target': t, **data}, indent=4))

        except WebSocketDisconnect:
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            print_exc()
    WS.disconnect(websocket)


WS.add('power/reboot')(lambda: cmd_run(['/usr/bin/systemctl', 'reboot', '-i']))

WS.add('power/shutdown')(lambda: cmd_run(['/usr/bin/systemctl', 'poweroff', '-i']))

WS.add('scheduler/asset')(
    lambda: WS.broadcast('scheduler/asset', items=Config.assets.serialize(), current=Config.assets.current.uuid))


@WS.add('scheduler/add_url')
async def add_url(items: list[str | dict] = []):
    for element in items:
        if isinstance(element, str):
            element = {'url': element}
        element.pop('uuid', None)  # uuid MUST be generated internally
        Config.assets.append(element)
    Config.save()


@WS.add('scheduler/add_file')
async def add_file(ws: WebSocket, items: list[str | dict] = []):
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
        await WS.send(ws, 'scheduler/add_file', error='Invalid elements', extra=invalid)


@WS.add('scheduler/asset/edit')
async def asset_edit(uuid: str,
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


@WS.add('scheduler/asset/current')
async def asset_current():
    if Config.enabled_asset_count:
        await WS.broadcast('scheduler/asset/current', uuid=Config.assets.current.uuid)


@WS.add('scheduler/delete')
async def asset_delete(uuid: str):
    del Config.assets[uuid]
    Config.save()


WS.add('scheduler/file')(lambda: WS.broadcast('scheduler/file', files=UPLOADS.serialize()))


@WS.add('scheduler/delete_file')
async def file_delete(files: list[str]):
    for file in files:
        UPLOADS.remove(file)
    Config.save()


@WS.add('scheduler/goto')
async def playlist_goto(index: Union[None, int, str] = None):
    Config.assets.goto_a(index)


WS.add('scheduler/goto/back')(lambda: Config.assets.prev_a())

WS.add('scheduler/goto/next')(lambda: Config.assets.next_a())


@WS.add('scheduler/reorder')
async def playlist_reorder(from_i: int, to_i: int):
    Config.assets.move(from_i, to_i)
    Config.save()


@WS.add('settings/default_duration')
async def settings_default_duration(duration: Optional[int] = None):
    if duration is not None:
        Config.def_duration = duration
        Config.save()
    await WS.broadcast('settings/default_duration', duration=Config.def_duration)


# @WS.add('settings/update')
# async def settings_update(do_upgrade: bool = False):
#     global APT_THREAD
#     if not APT_THREAD.is_alive():
#         loop = asyncio.get_running_loop()
#         def apt():
#             for line in apt_update(do_upgrade):
#                 if line == True:
#                     asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/start', upgrading=do_upgrade), loop)
#                 elif line == False:
#                     asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/end', upgrading=do_upgrade), loop)
#                 elif isinstance(line, tuple):
#                     asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/progress', upgrading=do_upgrade, is_stdout=line[0], data=line[1]), loop)

#         APT_THREAD = Thread(target=apt, name=('upgrade' if do_upgrade else 'update'))
#         APT_THREAD.start()
#         await WS.broadcast('settings/update/status', status=APT_THREAD.name, log=get_apt_log())

# WS.add('settings/update/list')(lambda: WS.broadcast('settings/update/list', updates=apt_list_upgrades()))

# WS.add('settings/update/reboot_required')(lambda: WS.broadcast('settings/update/reboot_required', reboot=reboot_required()))

# WS.add('settings/update/status')(lambda: WS.broadcast('settings/update/status', status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log()))

@WS.add('settings/audio/default')
async def settings_audio_default(device: Optional[str] = None):
    if device is not None:
        setDefaultAudioDevice(device)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())


WS.add('settings/audio/devices')(lambda: WS.broadcast('settings/audio/devices', devices=getAudioDevices()))


@WS.add('settings/audio/volume')
async def settings_audio_volume(device: Optional[str] = None, volume: Optional[float] = None):
    if volume is not None:
        setVolume(device, volume)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())


@WS.add('settings/audio/mute')
async def settings_audio_mute(device: Optional[str] = None, mute: Optional[bool] = None):
    if mute is not None:
        setMute(device, mute)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())


WS.add('settings/display/getBounds')(lambda: WS.broadcast('settings/display/getBounds', **WINDOW['bounds']))


@WS.add('settings/display/setBounds')
async def settings_display_boundsset(x: int, y: int, width: int, height: int):
    await UI_WS.broadcast('setBounds', x=x, y=y, width=width, height=height)


WS.add('settings/display/getOrientation')(
    lambda: WS.broadcast('settings/display/getOrientation', orientation=WINDOW['orientation']))


@WS.add('settings/display/setOrientation')
async def settings_display_orientationset(orientation: int):
    await UI_WS.broadcast('setOrientation', orientation=orientation)


WS.add('settings/display/getFlip')(lambda: WS.broadcast('settings/display/getFlip', flip=WINDOW['flip']))


@WS.add('settings/display/setFlip')
async def settings_display_flipset(flip: int):
    await UI_WS.broadcast('setFlip', flip=flip)


WS.add('settings/remote/get')(lambda: WS.broadcast('settings/remote/get',
                                                   remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                                                   remote_connected=REMOTE_CONNECTED,
                                                   remote_port=Config.remote_server_port,
                                                   remote_clients=list(Config.remote_clients.items())))


@WS.add('settings/remote/set')
async def remote_mode_set(remote_server: Optional[IPv4Address],
                          remote_port: Optional[PositiveInt] = const.default_port_secure):
    remote_port = remote_port or const.default_port_secure
    if Config.remote_server_ip == remote_server and Config.remote_server_port == remote_port:
        return
    Config.remote_server_ip = remote_server
    Config.remote_server_port = remote_port
    Config.remote_server_pk = None
    Config.save()
    for task in asyncio.all_tasks():
        if task.get_name() in ['page_controller', 'remote_control']:
            task.cancel()
    if remote_server:
        # noinspection PyAsyncCall
        asyncio.create_task(connect_to_server(remote_server, remote_port), name='remote_control')
    else:
        # noinspection PyAsyncCall
        asyncio.create_task(webview_control_main(), name='page_controller')
    await WS.broadcast('settings/remote/get',
                       remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                       remote_connected=REMOTE_CONNECTED,
                       remote_port=Config.remote_server_port,
                       remote_clients=list(Config.remote_clients.items()))


@WS.add('settings/remote/disconnect')
async def remote_disconnect(client: str):
    for remote in REMOTE_WS.active_connections:
        if remote.headers['instance_id'] == client:
            await remote.close(code=4023, reason="Server forced disconnection")
            REMOTE_WS.disconnect(remote)
            del Config.remote_clients[remote.headers['instance_id']]
            await WS.broadcast('settings/remote/get',
                               remote_server=Config.remote_server_ip.compressed if Config.remote_server_ip else None,
                               remote_connected=REMOTE_CONNECTED,
                               remote_port=Config.remote_server_port,
                               remote_clients=list(Config.remote_clients.items()))


@WS.add('settings/hostname')
async def settings_hostname(hostname: Optional[str] = None):
    if hostname is not None:
        set_hostname(hostname)
    await WS.broadcast('settings/hostname', hostname=get_hostname())


WS.add('settings/get_wifis')(lambda: WS.broadcast('settings/get_wifis', wifis=get_wifis()))


@WS.add('settings/netplan/file/new')
async def settings_netpfile_new(filename: str):
    create_netplan(filename)
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})


@WS.add('settings/netplan/file/set')
async def settings_netpfile_set(ws: WebSocket, filename: Optional[str] = None, content: str = '', apply: bool = True):
    global DEFAULT_AP
    if filename is not None:
        res = set_netplan(secure_filename(filename), content, apply)
    else:
        res = generate_netplan(apply)
    # noinspection PySimplifyBooleanCheck
    if res is True:
        if DEFAULT_AP and do_ip_addr(True):  # AP is still enabled, but now we are connected, AP is no longer needed
            stop_hostpot()
            DEFAULT_AP = None
            Config.assets.next_a()
    elif isinstance(res, str):
        await WS.send(ws, 'error', error='Netplan error', extra=res)
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})


@WS.add('settings/netplan/file/get')
async def settings_netpfile_get(filename: Optional[str] = None):
    netplan_files = get_netplan_file_list()
    if filename is not None:
        if filename in netplan_files:
            await WS.broadcast('settings/netplan/file/get', files={filename: get_netplan_file(filename)})
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in netplan_files})


@WS.add('settings/netplan/file/del')
async def settings_netpfile_del(ws: WebSocket, filename: Optional[str] = None, apply: bool = True):
    res = del_netplan_file(filename, apply)
    if isinstance(res, str):
        await WS.send(ws, 'error', error='Netplan error', extra=res)
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})


@WS.add('settings/cron/job')
async def settings_cronjob_get():
    CRONTAB.read()
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())


@WS.add('settings/cron/job/add')
async def settings_cronjob_add(cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
    CRONTAB.read()
    if CRONTAB.new(cmd, m, h, dom, mon, dow):
        CRONTAB.write()
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())


@WS.add('settings/cron/job/set_state')
async def settings_cronjob_state_set(ws: WebSocket, job: str, state: bool):
    CRONTAB.read()
    try:
        next(CRONTAB.find_comment(job)).enable(state)
        CRONTAB.write()
    except StopIteration:
        await WS.send(ws, 'error', error='Not found', extra='Requested job does not exist')
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())


@WS.add('settings/cron/job/delete')
async def settings_cronjob_delete(job: str):
    CRONTAB.read()
    CRONTAB.remove_all(comment=job)
    CRONTAB.write()
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())


@WS.add('settings/cron/job/edit')
async def settings_cronjob_edit(ws: WebSocket, job: str, cmd: str = '', m=None, h=None, dom=None, mon=None, dow=None):
    CRONTAB.read()
    try:
        _job = next(CRONTAB.find_comment(job))
        if all([x is not None for x in [m, h, dom, mon, dow]]):
            _job.setall(m, h, dom, mon, dow)
        if cmd == 'pwr':
            _job.set_command('/usr/sbin/poweroff')
        elif cmd == 'reb':
            _job.set_command('/usr/sbin/reboot')
        CRONTAB.write()
    except StopIteration:
        await WS.send(ws, 'error', error='Not found', extra='Requested job does not exist')
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())


@WWW.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password: str, username: str = Depends(LOGMAN)):
    Config.change_password(username, password)
    Config.save()
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']


@WWW.post("/api/scheduler/upload", status_code=status.HTTP_201_CREATED, dependencies=[Depends(LOGMAN)])
async def media_upload(files: list[UploadFile]):
    for infile in files:
        await UPLOADS.save(infile)


@WWW.get("/backup", dependencies=[Depends(LOGMAN)])
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
@WWW.post("/backup", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(LOGMAN)])
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


@WWW.delete('/backup', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(LOGMAN)])
async def factory_reset():
    Config.reset()

    CRONTAB.read()
    # noinspection PyProtectedMember
    CRONTAB.remove_all(comment=CRONTAB._cron_re)
    CRONTAB.write()

    await UI_WS.broadcast('reset', nocache=True)

    for file in UPLOADS.files:
        UPLOADS.remove(file)


@WWW.get("/", response_class=HTMLResponse)
async def scheduler(request: Request, username: str = Depends(LOGMAN)):
    return TEMPLATES.TemplateResponse('index.html.j2', dict(
        request=request,
        ut_vers=const.__version__,
        logged_user=username,
        hostname=get_hostname(),
        disp_size=WINDOW['bounds'],
        disk_used=UPLOADS.disk_usedh,  # type: ignore
        disk_total=UPLOADS.disk_totalh  # type: ignore
    ))


@WWW.get('/login')
async def login_page(request: Request, src: Optional[str] = '/'):
    try:
        await LOGMAN(request)
        # why are you trying to access login page from an authenticated session?
        return RedirectResponse(src or '/')
    except NotAuthenticatedException:
        pass

    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('login.html.j2', dict(
        request=request,
        src=src,
        ut_vers=const.__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname()
    ))


@WWW.get("/settings", response_class=HTMLResponse)
@WWW.get("/settings/{tab}", response_class=HTMLResponse)
async def settings(request: Request, tab: str = 'main_menu', username: str = Depends(LOGMAN)):
    data: dict[str, Any] = dict(
        request=request,
        ut_vers=const.__version__,
        logged_user=username,
        cur_tab=tab,
        disp_size=WINDOW['bounds'],
        disk_used=UPLOADS.disk_usedh,  # type: ignore
        disk_total=UPLOADS.disk_totalh,  # type: ignore
        def_wifi=DEF_WIFI_CARD
    )
    if tab == 'audio':
        data['audio'] = getAudioDevices()
    elif tab == 'display':
        data['displays'] = DISPLAYS
    elif tab == 'info':
        data['cpu_count'] = cpu_count()
        data['ram_tot'] = human_readable_size(virtual_memory().total)
        data['disks'] = [blk.model_dump() for blk in lsblk()]
        data['has_battery'] = sensors_battery() is not None
        data['temp_devs'] = {k: [x._asdict() for x in natsorted(v, key=lambda x: x.label)] for k, v in
                             sensors_temperatures().items()}
        data['fan_devs'] = {k: [x._asdict() for x in v] for k, v in sensors_fans().items()}

    return TEMPLATES.TemplateResponse(f'settings/{tab}.html.j2', data)


@WWW.api_route("/unitotem-{page}", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def first_boot_page(request: Request, page: Union[Literal['first-boot'], Literal['no-assets']]):
    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse(f'{page}.html.j2', dict(
        request=request,
        ut_vers=const.__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname(),
        wifi=DEFAULT_AP
    ))


async def connect_to_server(ip: IPv4Address, port: PositiveInt = const.default_port_secure, headers: dict = {}):
    global REMOTE_CONNECTED
    url = f'wss://{ip}:{port}/remote'
    headers.setdefault("instance_id", environ['instance_id'])
    headers.setdefault("hostname", get_hostname())
    headers.setdefault("port", cmdargs['bind'].split(':')[1])
    while not SHUTDOWN_EVENT.is_set():
        # noinspection PyBroadException
        try:
            print('Connecting to', url)
            if Config.remote_server_pk is None:
                server_pk = requests.get(f'https://{ip}:{port}/remote/public_key', verify=False).content
                Config.remote_server_pk = cast(rsa.RSAPublicKey, serialization.load_pem_public_key(server_pk))
                Config.save()
            # noinspection PyArgumentList
            async with asyncwebsockets.open_websocket(url, list(headers.items())) as ws:
                REMOTE_CONNECTED = True
                print('Connected to', url)
                while True:
                    # noinspection PyProtectedMember
                    msg = await ws._next_event()
                    if isinstance(msg, CloseConnection):
                        if msg.code == 4023:  # Server forced disconnection for unpairing
                            REMOTE_CONNECTED = False
                            # decorated function accepts a websocket as first argument, but we don't either have or
                            # need one the type checking of the IDE reports an error in the syntax due to an exceeding
                            # number of arguments, so we just suppress the error
                            await remote_mode_set(None, remote_server=None, remote_port=None)
                            return
                        break
                    data = loads(getattr(msg, 'data', '{}'))
                    if 'target' in data and '__signature__' in data:
                        Config.remote_server_pk.verify(
                            base64.b64decode(data['__signature__'].encode()),
                            data['src'].encode(),
                            padding.PSS(
                                mgf=padding.MGF1(hashes.SHA256()),
                                salt_length=padding.PSS.MAX_LENGTH
                            ),
                            hashes.SHA256()
                        )
                    if data.pop('target') == 'Show':
                        await UI_WS.broadcast('Show', False, **data)
                    if SHUTDOWN_EVENT.is_set():
                        break
        except asyncio.exceptions.CancelledError:
            print('Disconnected from remote server')
            break
        except InvalidSignature:
            print('Invalid signature, disconnected from server')
            await asyncio.sleep(5)
        except OSError as e:
            if e.args[0] == 'All connection attempts failed':
                print('Server unavailable, retrying in 5 seconds...')
            else:
                print_exc()
            await asyncio.sleep(5)
        except Exception:
            print_exc()
        REMOTE_CONNECTED = False


async def webview_control_main():
    print('Starting webview controller')
    async for asset in Config.assets.iter_wait(waiter=SHUTDOWN_EVENT):
        await WS.broadcast('scheduler/asset/current', uuid=asset.uuid)
        url = asset.url
        if url.startswith('file:'):
            url = 'https://localhost/uploaded/' + url.removeprefix('file:')
        data = dict(
            src=url,
            container=[None, 'web', 'image', 'video', 'audio'][asset.media_type + 1],
            fit=['contain', 'cover', 'fill'][asset.fit],
            bg_color=asset.bg_color.as_rgb() if asset.bg_color is not None else None
        )
        await UI_WS.broadcast('Show', False, **data)
        await REMOTE_WS.broadcast('Show', False, **data)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('--no-gui', action='store_true',
                        help='Start UniTotem Manager without webview gui (for testing)')
    parser.add_argument('--bind', default=const.default_bind_secure)
    parser.add_argument('--insecure_bind', default=const.default_bind)
    parser.add_argument('--remote')
    parser.add_argument('--config', default=const.default_config_file)
    parser.add_argument('--version', action='version', version='%(prog)s ' + const.__version__)
    cmdargs = vars(parser.parse_args())

    try:
        Config(filename=cmdargs['config'])
    except FileNotFoundError:
        print('First boot or no configuration file found.')
        # noinspection PyBroadException
        try:
            if not do_ip_addr(True) or exists(FALLBACK_AP_FILE):
                # config file doesn't exist, and we are not connected, maybe it's first boot
                hotspot = start_hotspot()
                DEFAULT_AP = dict(ssid=hotspot[0], password=hotspot[1], qrcode=wifi_qr(hotspot[0], hotspot[1]))
                print(
                    f'Not connected to any network, started fallback hotspot {hotspot[0]} with password {hotspot[1]}.')
        except Exception:
            print("Couldn't start wifi hotspot.")
            print_exc()

    REMOTE_WS.pk = Config.rsa_pk

    # APT_THREAD.start()

    uvloop.install()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda *_: SHUTDOWN_EVENT.set())

    Config.assets.set_callback(lambda assets, current: WS.broadcast('scheduler/asset', items=assets, current=current),
                               loop)

    UPLOADS._evloop = loop

    observer = Observer()
    # noinspection PyTypeChecker
    observer.schedule(UPLOADS, UPLOADS.folder)
    observer.start()

    UPLOADS.scan_folder()

    # if cmdargs.get('remote'):
    #     loop.create_task(connect_to_server(cmdargs['remote']), name='remote_control')
    # el
    if Config.remote_server_ip:
        loop.create_task(connect_to_server(Config.remote_server_ip, Config.remote_server_port), name='remote_control')
    elif not cmdargs.get('no_gui', False):
        loop.create_task(webview_control_main(), name='page_controller')


    async def info_loop(ws: WSManager, waiter: asyncio.Event):
        while not waiter.is_set():
            await broadcast_sysinfo(ws)
            await asyncio.sleep(3)


    loop.create_task(info_loop(WS, SHUTDOWN_EVENT), name='info_loop')

    loop.create_task(serve(WWW, HyperConfig().from_mapping(  # type: ignore
        bind=cmdargs['bind'], insecure_bind=cmdargs['insecure_bind'],
        certfile=const.certfile, keyfile=const.keyfile,
        accesslog='-', errorlog='-', loglevel='INFO'
    ), shutdown_trigger=SHUTDOWN_EVENT.wait), name='server')  # type: ignore

    loop.run_forever()

    stop_hostpot()

    observer.stop()
    # APT_THREAD.join()
