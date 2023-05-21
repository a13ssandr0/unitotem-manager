import asyncio
from argparse import ArgumentParser
from datetime import datetime, timedelta
from io import BytesIO
from json import dumps, loads
from math import inf
from os import environ, listdir, makedirs, remove, urandom
from os.path import dirname, exists, isfile, join, normpath
from pathlib import Path
from subprocess import run
from threading import Thread
from time import strftime, time
from traceback import format_exc, print_exc
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import load_dotenv, set_key
from fastapi import (Depends, FastAPI, HTTPException, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect, status)
from fastapi.middleware import Middleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import Mount
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_login import LoginManager
from fastapi_login.exceptions import InvalidCredentialsException
from jwt import InvalidSignatureError
from natsort import natsorted
from psutil import (boot_time, disk_usage, sensors_battery, sensors_fans,
                    sensors_temperatures, virtual_memory)
from utils import *
from uvicorn import Config as uvConfig
from uvicorn import Server as uvServer
from uvicorn import run
from validators import url as is_valid_url
from werkzeug.utils import secure_filename

VERSION = '2.3.0'


APT_THREAD        = Thread(target=apt_update, name='update')
CFG_FILE          = Path('/etc/unitotem/unitotem.conf')
CURRENT_ASSET     = -1
DEF_WIFI_CARD     = get_ifaces(IF_WIRELESS)[0]
DEFAULT_AP        = None
DISPLAY           = None
load_dotenv('/etc/unitotem/unitotem.env')
if 'auth_token' not in environ:
    environ['auth_token'] = urandom(24).hex()
    Path('/etc/unitotem/unitotem.env').touch(mode=0o600)
    set_key('/etc/unitotem/unitotem.env', 'auth_token', environ['auth_token'])
LOGMAN            = LoginManager(environ['auth_token'], custom_exception=NotAuthenticatedException,
                        token_url='/auth/token', use_cookie=True, use_header=False, default_expiry=timedelta(hours=5))
NEXT_CHANGE_TIME  = 0
OS_VERSION        = os_version()
TEMPLATES         = Jinja2Templates(directory=join(dirname(__file__), 'templates'))
TEMPLATES.env.filters['flatten'] = flatten
UI_WS             = ConnectionManager(True)
UPLOAD_FOLDER     = join(dirname(__file__), 'uploaded')
WS                = ConnectionManager()
WWW = FastAPI(
    title='UniTotem', version=VERSION,
    routes=[
        Mount('/static', StaticFiles(directory=join(dirname(__file__), 'static')), name='static'),
        Mount('/uploaded', StaticFiles(directory=UPLOAD_FOLDER), name='uploaded')
    ],
    exception_handlers={
        InvalidSignatureError: login_redir,
        NotAuthenticatedException: login_redir
    }
)


def list_resources():
    return list(filter(lambda f: isfile(join(UPLOAD_FOLDER, f)), listdir(UPLOAD_FOLDER)))

def get_resources():
    infos = []
    for f in list_resources():
        infos.append(get_file_info(UPLOAD_FOLDER, f, def_dur=Config.def_duration))
    return infos


@LOGMAN.user_loader() # type: ignore
async def load_user(username:str):
    return username if username in Config.users else None




@WWW.post(LOGMAN.tokenUrl)
async def login(data: LoginForm = Depends()):
    if not Config.authenticate(data.username, data.password):
        raise InvalidCredentialsException
    expires_delta = timedelta(hours=1)
    access_token = LOGMAN.create_access_token(data={'sub':data.username}, expires=expires_delta)
    resp = RedirectResponse(data.src, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key=LOGMAN.cookie_name, value=access_token, httponly=False,
                    samesite='strict', max_age=int(expires_delta.total_seconds()))
    return resp


@WWW.websocket("/ui_ws")
async def ui_websocket(websocket: WebSocket):
    global DISPLAY
    await UI_WS.connect(websocket)
    while True:
        try:
            data = await websocket.receive_json()
            match data['target']:
                case 'getAllDisplays':
                    await WS.broadcast('settings/display/all', bounds=data['displays'])
                case 'getBounds':
                    DISPLAY = data['bounds']
                    await WS.broadcast('settings/display/bounds', bounds=DISPLAY)
                case 'getAllowInsecureCerts':
                    await WS.broadcast('settings/display/allowInsecureCerts', bounds=data['allow'])
        except WebSocketDisconnect:
            UI_WS.disconnect(websocket)
            break

@WWW.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global NEXT_CHANGE_TIME, CURRENT_ASSET, APT_THREAD
    
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
        try:
            data = await websocket.receive_json()
            match data['target']:

                case 'power/reboot':
                    run('/usr/sbin/reboot')

                case 'power/shutdown':
                    run('/usr/sbin/poweroff')

                case 'scheduler/asset':
                    # check_attrs(request_data, [('add_asset', dict[str, dict[str, Union[int, bool]]])])
                    if 'items' in data:
                        save = False
                        invalid = []
                        for element, attrs in data['items'].items():
                            if is_valid_url(element) or element in list_resources():
                                Config.add_asset(
                                    url= ('file:' if element in list_resources() else '') + element,
                                    duration= attrs.get('duration', None) or Config.def_duration,
                                    enabled= attrs.get('enabled', False),
                                )
                                save = True
                            else: invalid.append(element)
                        if save: 
                            Config.save()
                        if invalid:
                            await WS.send(websocket, 'scheduler/asset', error='Invalid elements', extra=invalid)
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)

                case 'scheduler/validate_url':
                    await WS.send(websocket, 'scheduler/validate_url', valid=bool(is_valid_url(data['url'])))

                case 'scheduler/asset/current':
                    if CURRENT_ASSET >= 0:
                        await WS.broadcast('scheduler/asset/current', uuid=Config.assets[CURRENT_ASSET].uuid)

                case 'scheduler/set_state':
                    index, asset = Config.get_asset(data['uuid'])
                    start_rotation = not Config.enabled_asset_count
                    asset.enabled = data['state']
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
                    Config.save()
                    if start_rotation:
                        await webview_goto(0)
                    elif index == CURRENT_ASSET:
                        NEXT_CHANGE_TIME = 0

                case 'scheduler/update_duration':
                    index, asset = Config.get_asset(data['uuid'])
                    old_dur = asset.duration
                    asset.duration = data['duration']
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
                    Config.save()
                    if index == CURRENT_ASSET:
                        NEXT_CHANGE_TIME += (asset.duration or inf) - old_dur # type: ignore

                case 'scheduler/delete':
                    index, asset = Config.get_asset(data['uuid'])
                    if index == CURRENT_ASSET:
                        NEXT_CHANGE_TIME = 0
                    Config.remove_asset(asset)
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
                    Config.save()

                case 'scheduler/file':
                    await WS.broadcast('scheduler/file', files=get_resources())

                case 'scheduler/delete_file':
                    for file in data['files']:
                        if exists(join(UPLOAD_FOLDER, file)):
                            for index, asset in Config.find_assets('file:' + file):
                                if index == CURRENT_ASSET:
                                    NEXT_CHANGE_TIME = 0
                                try:
                                    Config.remove_asset(asset)
                                except ValueError:
                                    pass #element to delete is not in schedule
                            remove(join(UPLOAD_FOLDER, file))
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
                    await WS.broadcast('scheduler/file', files=get_resources())
                    Config.save()

                case 'scheduler/goto':
                    await webview_goto(data.get('index', CURRENT_ASSET), force=data.get('force', 'index' in data))

                case 'scheduler/goto/back':
                    await webview_goto(CURRENT_ASSET-1, backwards=True)

                case 'scheduler/goto/next':
                    await webview_goto(CURRENT_ASSET+1)

                case 'scheduler/reorder':
                    Config.move_asset(data['from'], data['to'])
                    await WS.broadcast('scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
                    Config.save()
                    if CURRENT_ASSET in [data['to'], data['from']]:
                        await webview_goto()  #show new asset

                case 'settings/default_duration':
                    if 'duration' in data:
                        Config.def_duration = data['duration']
                        Config.save()
                    await WS.broadcast('settings/default_duration', duration=Config.def_duration)

                case 'settings/update':
                    if not APT_THREAD.is_alive():
                        loop = asyncio.get_running_loop()
                        def apt():
                            for line in apt_update(data.get('do_upgrade', False)):
                                if line == True:
                                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/start', upgrading=data.get('do_upgrade', False)), loop)
                                elif line == False:
                                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/end', upgrading=data.get('do_upgrade', False)), loop)
                                elif isinstance(line, tuple):
                                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/progress', upgrading=data.get('do_upgrade', False), is_stdout=line[0], data=line[1]), loop)

                        APT_THREAD = Thread(target=apt, name=('upgrade' if data.get('do_upgrade', False) else 'update'))
                        APT_THREAD.start()
                        await WS.broadcast('settings/update/status', status=APT_THREAD.name, log=get_apt_log())

                case 'settings/update/list':
                    await WS.broadcast('settings/update/list', updates=apt_list_upgrades())
                        
                case 'settings/update/status':
                    await WS.broadcast('settings/update/status', status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log())

                case 'settings/audio/default':
                    if 'device' in data:
                        setDefaultAudioDevice(data['device'])
                    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

                case 'settings/audio/devices':
                    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

                case 'settings/audio/volume':
                    if 'volume' in data:
                        setVolume(data.get('device'), data['volume'])
                    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

                case 'settings/audio/mute':
                    if 'mute' in data:
                        setMute(data.get('device'), data['mute'])
                    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

                case 'settings/hostname':
                    if 'hostname' in data:
                        set_hostname(data['hostname'])
                    await WS.broadcast('settings/hostname', hostname=get_hostname())

                case 'settings/get_wifis':
                    await WS.broadcast('settings/get_wifis', wifis=get_wifis())

                case 'settings/netplan/file/new':
                    create_netplan(data['filename'])
                    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

                case 'settings/netplan/file/set':
                    if 'filename' in data:
                        res = set_netplan(secure_filename(data['filename']), data.get('content', ''), data.get('apply', True))
                    else:
                        res = generate_netplan(data.get('apply', True))
                    if res == True:
                        if DEFAULT_AP and do_ip_addr(True): #AP is still enabled but now we are connected, AP is no longer needed
                            stop_hostpot()
                            NEXT_CHANGE_TIME = 0
                            await webview_goto(0)
                    elif isinstance(res, str):
                        await WS.send(websocket, 'error', error='Netplan error', extra=res)
                    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

                case 'settings/netplan/file/get':
                    netplan_files = get_netplan_file_list()
                    if 'filename' in data:
                        if data['filename'] in netplan_files:
                            await WS.broadcast('settings/netplan/file/get', files={data['filename']: get_netplan_file(data['filename'])})
                    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in netplan_files})

                case 'settings/netplan/file/del':
                    res = del_netplan_file(data['filename'], data.get('apply', True))
                    if isinstance(res, str):
                        await WS.send(websocket, 'error', error='Netplan error', extra=res)
                    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

                case 'settings/cron/job':
                    CRONTAB.read()
                    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

                case 'settings/cron/job/add':
                    CRONTAB.read()
                    if CRONTAB.new(**data):
                        CRONTAB.write()
                    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

                case 'settings/cron/job/set_state':
                    CRONTAB.read()
                    try:
                        next(CRONTAB.find_comment(data['job'])).enable(data['state'])
                        CRONTAB.write()
                    except StopIteration:
                        await WS.send(websocket, 'error', error='Not found', extra='Requested job does not exist')
                    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

                case 'settings/cron/job/delete':
                    CRONTAB.read()
                    CRONTAB.remove_all(comment=data['job'])
                    CRONTAB.write()
                    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

                case 'settings/cron/job/edit':
                    CRONTAB.read()
                    try:
                        job = next(CRONTAB.find_comment(data['job']))
                        if all([x in data and data[x] != None for x in ['m', 'h', 'dom', 'mon', 'dow']]):
                            job.setall(data['m'],data['h'],data['dom'],data['mon'],data['dow'])
                        if data.get('cmd') == 'pwr':
                            job.set_command('/usr/sbin/poweroff')
                        elif data.get('cmd') == 'reb':
                            job.set_command('/usr/sbin/reboot')
                        CRONTAB.write()
                    except StopIteration:
                        await WS.send(websocket, 'error', error='Not found', extra='Requested job does not exist')
                    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())

                case 'settings/info':
                    cpu_percent = []
                    for x in cpu_times_percent(None):
                        y = x._asdict()
                        y['total'] = round(sum(x)-x.idle-x.guest-x.guest_nice-x.iowait,1)
                        cpu_percent.append(y)
                    # cpu_count()
                    await WS.send(
                        websocket, 'settings/info',
                        uptime = str(datetime.fromtimestamp(time()) - datetime.fromtimestamp(boot_time())).split('.')[0],
                        cpu = cpu_percent,
                        battery = sensors_battery()._asdict() if sensors_battery() else None,
                        fans = [x._asdict() for v in sensors_fans().values() for x in v],
                        temperatures = {k:[x._asdict() for x in natsorted(v, key=lambda x: x.label)] for k,v in sensors_temperatures().items()},
                        vmem = virtual_memory()._asdict(),
                    )

                case _:
                    await WS.send(websocket, 'error', error='Invalid command', extra=dumps(data, indent=4))

        except WebSocketDisconnect:
            WS.disconnect(websocket)
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            print_exc()
    WS.disconnect(websocket)


# def check_attrs(d: dict, attr: list[tuple[str, type | tuple[type]]]):
#     missing = []
#     mistyped = []
#     for att, typ in attr:
#         if att not in d:
#             missing.append(att)
#         elif not isinstance(d[att], typ):
#             mistyped.append((att, typ))
#     return missing, mistyped

@WWW.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password:str, username: str = Depends(LOGMAN)):
    Config.change_password(username, password)
    Config.save()
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']


@WWW.post("/api/scheduler/upload")
async def media_upload(response: Response, files: list[UploadFile], username: str = Depends(LOGMAN)):
    makedirs(UPLOAD_FOLDER, exist_ok=True)
    for infile in files:
        if infile and infile.filename:
            with open(join(UPLOAD_FOLDER, secure_filename(infile.filename)), 'bw') as out:
                while True:
                    buf = await infile.read(64 * 1024 * 1024) #64MB buffer
                    if not buf: break
                    out.write(buf)

            file_data = get_file_info(UPLOAD_FOLDER, secure_filename(infile.filename), def_dur=Config.def_duration)
            Config.add_asset(
                url= 'file:' + file_data['filename'],
                duration= file_data.get('duration_s'),
                enabled= False,
            )
            Config.save()
            await WS.broadcast(target='scheduler/asset', items=Config.assets_json(), current=Config.assets[CURRENT_ASSET].uuid if CURRENT_ASSET>=0 else None)
    response.status_code = status.HTTP_201_CREATED

@WWW.get("/backup")
async def create_backup(include_uploaded: bool = False, username: str = Depends(LOGMAN)):
    global VERSION, UPLOAD_FOLDER
    config_backup = {
        "version": VERSION,
        "CONFIG": Config.json(),
        "hostname": get_hostname(),
        # "def_audio_dev": get_default_audio_device(),
        "netplan": {fname: get_netplan_file(fname) for fname in get_netplan_file_list()}
        # cron
    }

    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, 'w', ZIP_DEFLATED, False) as zip_file:
        zip_file.writestr("config.json", dumps(config_backup))
        if include_uploaded:
            for file_name in list_resources():
                zip_file.write(join(UPLOAD_FOLDER, file_name), "uploaded/" + file_name)
    zip_buffer.seek(0)
    return Response(content=zip_buffer, media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="' + strftime('unitotem-manager-%Y%m%d-%H%M%S.zip') + '"'})

@WWW.post("/backup", status_code=status.HTTP_204_NO_CONTENT)
async def load_backup(backup_file: UploadFile, options: dict[str, bool], username: str = Depends(LOGMAN)):
    global CURRENT_ASSET, NEXT_CHANGE_TIME, UPLOAD_FOLDER
    if backup_file.content_type not in ['application/octet-stream', 'application/zip']:
        with ZipFile(backup_file.stream._file) as zip_file:
            files = zip_file.namelist()
            if 'config.json' in files:
                config_json = loads(zip_file.read('config.json'))
                if 'CONFIG' in config_json and options.get('CONFIG', True):
                    Config.parse_obj(config_json['CONFIG'])
                    CURRENT_ASSET    = -1
                    NEXT_CHANGE_TIME = 0
                    Config.save()
                if 'hostname' in config_json and options.get('hostname', True):
                    set_hostname(config_json['hostname'])
                if 'def_audio_dev' in config_json and options.get('def_audio_dev', True):
                    # set_audio_device(config_json['def_audio_dev'])
                    pass
                if 'netplan' in config_json and options.get('netplan', True):
                    res = set_netplan(filename = None, file_content=config_json['netplan'])
            if options.get('uploaded', True):
                for file in files:
                    if file.startswith('uploaded/'):
                        zip_file.extract(file, normpath(join(UPLOAD_FOLDER, '..')))
            if 'res' in locals():
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=res)
    else:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail='Wrong mimetype: ' + backup_file.content_type)
            
@WWW.delete('/backup', status_code=status.HTTP_204_NO_CONTENT)
async def factory_reset(username: str = Depends(LOGMAN)):
    global CURRENT_ASSET, NEXT_CHANGE_TIME
    Config.reset()
    # if isfile(ASOUND_CONF): remove(ASOUND_CONF)
    for res in list_resources(): remove(join(UPLOAD_FOLDER, res))
    CURRENT_ASSET    = -1
    NEXT_CHANGE_TIME = 0

@WWW.get("/", response_class=HTMLResponse)
async def scheduler(request: Request, username:str = Depends(LOGMAN)):
    return TEMPLATES.TemplateResponse('index.html.j2', dict(
        request=request,
        ut_vers=VERSION,
        logged_user=username,
        hostname=get_hostname(),
        disp_size=DISPLAY,
        disk_used=human_readable_size(disk_usage(UPLOAD_FOLDER).used),
        disk_total=human_readable_size(disk_usage(UPLOAD_FOLDER).total)
    ))

@WWW.get('/login')
async def login_page(request: Request, src:str|None = '/'):
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
        ut_vers=VERSION,
        os_vers=OS_VERSION,
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname()
    ))

@WWW.get("/settings", response_class=HTMLResponse)
@WWW.get("/settings/{tab}", response_class=HTMLResponse)
async def settings(request: Request, tab: str = 'main_menu', username: str = Depends(LOGMAN)):
    data = dict(
        request=request,
        ut_vers=VERSION,
        logged_user=username,
        cur_tab=tab,
        disp_size=DISPLAY,
        disk_used=human_readable_size(disk_usage(UPLOAD_FOLDER).used),
        disk_total=human_readable_size(disk_usage(UPLOAD_FOLDER).total),
        def_wifi=DEF_WIFI_CARD
    )
    if tab == 'info':
        data['disks'] = [blk.dict() for blk in lsblk()] # type: ignore
        # data['net_addrs'] = do_ip_addr() # type: ignore
        data['has_battery'] = sensors_battery() != None
        data['has_fans'] = bool(sensors_fans()) # type: ignore
    
    return TEMPLATES.TemplateResponse(f'settings/{tab}.html.j2', data)

@WWW.get('/base', response_class=HTMLResponse)
async def base_page(request: Request):
    return TEMPLATES.TemplateResponse('common/html/base.html.j2', dict(
        request=request,
        ut_vers=VERSION,
        # logged_user=username,
        hostname=get_hostname(),
        disp_size=DISPLAY,
        disk_used=human_readable_size(disk_usage(UPLOAD_FOLDER).used),
        disk_total=human_readable_size(disk_usage(UPLOAD_FOLDER).total)
    ))


@WWW.get("/unitotem-no-assets", response_class=HTMLResponse)
async def no_assets_page(request: Request):
    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('no-assets.html.j2', dict(
        request=request,
        ut_vers=VERSION,
        os_vers=OS_VERSION,
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname()
    ))

@WWW.get("/unitotem-first-boot", response_class=HTMLResponse)
async def first_boot_page(request: Request):
    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('first-boot.html.j2', dict(
        request=request,
        ut_vers=VERSION,
        os_vers=OS_VERSION,
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname(),
        wifi=DEFAULT_AP
    ))

async def sock_send(target, **kwargs):
    await UI_WS.broadcast(target, **kwargs)
    print(kwargs)

async def webview_goto(asset: int = CURRENT_ASSET, force: bool = False, backwards: bool = False):
    global NEXT_CHANGE_TIME, CURRENT_ASSET, WS
    if 0 <= asset < len(Config.assets):
        if Config.assets[asset].enabled or force:
            url = Config.assets[CURRENT_ASSET := asset].url
            if url.startswith('file:'):
                url = 'https://localhost/uploaded/' + url.removeprefix('file:')
            await sock_send('Show', src=url)
            NEXT_CHANGE_TIME = int(time()) + (Config.assets[CURRENT_ASSET].duration or inf)
            await WS.broadcast('scheduler/asset/current', uuid=Config.assets[CURRENT_ASSET].uuid)
        else:
            await webview_goto(asset + (-1 if backwards else 1))

async def webview_control_main():
    global NEXT_CHANGE_TIME, CURRENT_ASSET, UI_WS
    while True:
        if time()>=NEXT_CHANGE_TIME:
            if Config.first_boot:
                NEXT_CHANGE_TIME = inf
                CURRENT_ASSET = -1
                await WS.broadcast('scheduler/asset/current', uuid=None)
                await sock_send('Show', src='https://localhost/unitotem-first-boot', container='web')
            elif not Config.enabled_asset_count:
                NEXT_CHANGE_TIME = inf
                CURRENT_ASSET = -1
                await WS.broadcast('scheduler/asset/current', uuid=None)
                await sock_send('Show', src='https://localhost/unitotem-no-assets', container='web')
            else:
                CURRENT_ASSET += 1
                if CURRENT_ASSET >= len(Config.assets): CURRENT_ASSET = 0
                await webview_goto(CURRENT_ASSET)
        await asyncio.sleep(1)





if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('--no-gui', action='store_true', help='Start UniTotem Manager without webview gui (for testing)')
    parser.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    cmdargs = vars(parser.parse_args())


    try:
        Config.parse_file_('/etc/unitotem/unitotem.conf')
    except FileNotFoundError:
        print('First boot or no configuration file found.')
        if (not do_ip_addr(True) or exists(FALLBACK_AP_FILE)):
            # config file doesn't exist and we are not connected, maybe it's first boot
            hotspot = start_hotspot()
            DEFAULT_AP = dict(ssid=hotspot[0], password = hotspot[1], qrcode = wifi_qr(hotspot[0], hotspot[1]))
            print(f'Not connected to any network, started fallback hotspot {hotspot[0]} with password {hotspot[1]}.')


    APT_THREAD.start()

    loop = asyncio.new_event_loop()

    if not cmdargs.get('no_gui', False):
        loop.create_task(webview_control_main(), name='page_controller')

    loop.create_task(uvServer(uvConfig(FastAPI(middleware=[Middleware(HTTPSRedirectMiddleware)]), port=80)).serve(), name='http_server')

    Thread(target=loop.run_forever, daemon=True).start()

    run(WWW, port=443, ssl_certfile="/etc/ssl/unitotem.pem")
    

    stop_hostpot()