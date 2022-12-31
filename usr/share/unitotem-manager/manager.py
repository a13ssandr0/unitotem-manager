from argparse import ArgumentParser
from copy import copy
from hmac import compare_digest
from io import BytesIO
from json import JSONDecodeError, dumps, loads
from os import makedirs, listdir, remove
from os.path import exists, getsize, isfile, join, normpath
from shutil import disk_usage
from subprocess import run
from threading import Thread
from time import sleep, strftime, time
from traceback import format_exc
from typing import Union
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile
from os.path import dirname
from sys import argv
from crontab import CronTab
from dasbus.connection import SystemMessageBus
from dasbus.identifier import DBusServiceIdentifier
from flask import Flask, render_template, send_file
from flask_httpauth import HTTPBasicAuth
from pymediainfo import MediaInfo
import utils as u
from validators import url as is_valid_url
from uvicorn import run as uvicorn_run
from fastapi import FastAPI, Request, UploadFile, Depends, HTTPException, status, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.routing import Mount
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware import Middleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from threading import Thread
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from fastapi.templating import Jinja2Templates

VERSION = '2.3.0'


APT_THREAD        = Thread()
AUTH              = HTTPBasic()
CFG_DIR           = '/etc/unitotem/'
CFG_FILE          = join(CFG_DIR, 'unitotem.conf')
CONFIG_DEF        = {'urls': [],'default_duration': 30, 'users': {'admin': {'pass': generate_password_hash('admin')}}}
CONFIG            = copy(CONFIG_DEF)
CURRENT_ASSET     = -1
DEF_WIFI_CARD     = u.get_ifaces(u.IF_WIRELESS)[0]
DEFAULT_AP        = None
IS_FIRST_BOOT     = False
NEXT_CHANGE_TIME  = 0
OS_VERSION        = u.os_version()
STATIC_FOLDER     = join(dirname(argv[0]), 'static')
TEMPLATES         = Jinja2Templates(directory=join(dirname(argv[0]), 'templates'))

UI_BUS = DBusServiceIdentifier(
    namespace=('org', 'unitotem', 'viewer'),
    message_bus=SystemMessageBus()
).get_proxy()

UPLOAD_FOLDER   = join(dirname(argv[0]), 'static', 'uploaded')

WWW = FastAPI(
    title='UniTotem', version=VERSION,
    middleware=[
        Middleware(HTTPSRedirectMiddleware)
    ],
    routes=[
        Mount('/static', StaticFiles(directory=STATIC_FOLDER), name='static'),
        Mount('/uploaded', StaticFiles(directory=UPLOAD_FOLDER), name='uploaded')
    ]
)
# WWW.config['TEMPLATES_AUTO_RELOAD'] = True
# UPLOADED_FOLDER         = join(WWW.static_folder, 'uploaded')


def save_config():
    global CONFIG, IS_FIRST_BOOT
    with open(CFG_FILE, 'w') as conf_f:
        conf_f.write(dumps(CONFIG, indent=4))
        IS_FIRST_BOOT = False

def load_config():
    if not exists(CFG_FILE): return False
    global CONFIG
    should_update_file = False
    with open(CFG_FILE, 'r') as conf_f:
        try:
            cfg_tmp = loads(conf_f.read())
            for k, v in CONFIG.items():
                if k not in cfg_tmp:
                    cfg_tmp[k] = v
                    should_update_file = True
            CONFIG = cfg_tmp
        except JSONDecodeError:
            should_update_file = True
    if should_update_file:
        save_config()
    return True

def enabled_asset_count():
    cnt = 0
    for e in CONFIG['urls']:
        if e['enabled']:
            cnt += 1
    return cnt

def human_readable_size(size, decimal_places=2):
    for unit in ['B','KiB','MiB','GiB','TiB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{unit}"

def find_by_attribute(l: list, key: any, value: any):
    for index, elem in enumerate(l):
        if key in elem and elem[key] == value:
            return index
    else:
        raise ValueError(f'No element with {repr(key)}: {repr(value)} in list')

def list_resources():
    return [f for f in listdir(UPLOAD_FOLDER) if isfile(join(UPLOAD_FOLDER, f))]

def get_resources(name=None):
    def get_file_info(f):
        if isfile(join(UPLOAD_FOLDER, f)):
            dur = ''
            dur_s = ''
            for track in MediaInfo.parse(join(UPLOAD_FOLDER, f)).tracks:
                if 'other_duration' in track.to_data():
                    dur = track.to_data()['other_duration'][1]
                    dur_s = round(int(track.to_data()['duration'])/1000)
                    break
                elif 'duration' in track.to_data():
                    dur = track.to_data()['duration']
                    dur_s = round(int(dur)/1000)
                    break
            return {
                'filename': f,
                'size': human_readable_size(getsize(join(UPLOAD_FOLDER, f))),
                'duration': dur,
                'duration_s': dur_s
            }

    if name:
        return get_file_info(name)
    else:
        return list(map(get_file_info, listdir(UPLOAD_FOLDER)))



def get_current_username(credentials: HTTPBasicCredentials = Depends(AUTH)):
    if not check_password_hash(CONFIG['users'].get(credentials.username, {}).get('pass', ''), credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@WWW.get("/", response_class=HTMLResponse)
def scheduler(request: Request, response: Response, username: str = Depends(get_current_username)):
    return TEMPLATES.TemplateResponse('index.html', dict(
        request=request,
        ut_vers=VERSION,
        logged_user=username,
        hostname=u.get_hostname(),
        disp_size=u.get_display_size(),
        disk_used=human_readable_size(disk_usage(UPLOAD_FOLDER).used),
        disk_total=human_readable_size(disk_usage(UPLOAD_FOLDER).total),
        urls_list=CONFIG['urls'],
        default_duration=CONFIG['default_duration'],
        files_list=get_resources()
    ))

@WWW.get("/settings", response_class=HTMLResponse)
def settings(request: Request, response: Response, username: str = Depends(get_current_username)):
    return TEMPLATES.TemplateResponse('settings.html', dict(
        request=request,
        ut_vers=VERSION,
        logged_user=username,
        disp_size=u.get_display_size(),
        disk_used=human_readable_size(disk_usage(UPLOAD_FOLDER).used),
        disk_total=human_readable_size(disk_usage(UPLOAD_FOLDER).total),
        upd=u.get_upd_count(),
        is_updating=APT_THREAD.name == 'update' and APT_THREAD.is_alive(),
        is_upgrading=APT_THREAD.name == 'upgrade' and APT_THREAD.is_alive(),
        hostname=u.get_hostname(),
        netplan_config={fname.removesuffix('.yaml'): u.get_netplan_file(fname) for fname in u.get_netplan_file_list()},
        default_duration=CONFIG['default_duration'],
        # audio=get_audio_devices(),
        # def_audio_dev=get_default_audio_device(),
        crontab=[job for job in CronTab(user='root').crons if job.comment.startswith('unitotem:-)')],
        def_wifi=DEF_WIFI_CARD
    ))



class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()

@WWW.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    username = get_current_username(await AUTH(websocket))
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            await manager.send_personal_message(dumps(handle_api(data, username)), websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        # await manager.broadcast(f"Client #{client_id} left the chat")
    except Exception:
        await manager.send_personal_message(dumps({'error': True, 'extra': format_exc()}), websocket)


# def check_attrs(d: dict, attr: list[tuple[str, type | tuple[type]]]):
#     missing = []
#     mistyped = []
#     for att, typ in attr:
#         if att not in d:
#             missing.append(att)
#         elif not isinstance(d[att], typ):
#             mistyped.append((att, typ))
#     return missing, mistyped



def handle_api(request_data, username) -> dict[str, any]:
    global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG, IS_FIRST_BOOT, APT_THREAD
    
    response = {'error': None, 'extra': None}

    if 'reboot' in request_data:
        run('/usr/sbin/reboot')

    elif 'shutdown' in request_data:
        run('/usr/sbin/poweroff')

    elif 'add_asset' in request_data:
        # check_attrs(request_data, [('add_asset', dict[str, dict[str, Union[int, bool]]])])
        save = False
        invalid = []
        for element, attrs in request_data['add_asset'].items():
            if is_valid_url(element) or element in list_resources():
                CONFIG['urls'].append({
                    'url': ('file:' if element in list_resources() else '') + element,
                    'duration': attrs.get('duration', None) or CONFIG['default_duration'],
                    'enabled': attrs.get('enabled', False),
                    'uuid': str(uuid4())
                })
                save = True
            else: invalid.append(element)
        if save: save_config()
        if invalid:
            response = {'error': 'Invalid elements', 'extra': invalid}

    elif 'set_state' in request_data:
        index = find_by_attribute(CONFIG['urls'], 'uuid', request_data['uuid'])
        start_rotation = not enabled_asset_count()
        CONFIG['urls'][index]['enabled'] = request_data['set_state']
        save_config()
        if start_rotation:
            webview_goto(0)
        elif index == CURRENT_ASSET:
            NEXT_CHANGE_TIME = 0

    elif 'update_duration' in request_data:
        index = find_by_attribute(CONFIG['urls'], 'uuid', request_data['uuid'])
        old_dur = CONFIG['urls'][index]['duration']
        CONFIG['urls'][index]['duration'] = request_data['update_duration']
        save_config()
        if index == CURRENT_ASSET:
            NEXT_CHANGE_TIME += (CONFIG['urls'][index]['duration'] or float('inf')) - old_dur

    elif 'delete' in request_data:
        index = find_by_attribute(CONFIG['urls'], 'uuid', request_data['uuid'])
        if index == CURRENT_ASSET:
            NEXT_CHANGE_TIME = 0
        CONFIG['urls'].pop(index)
        save_config()

    elif 'delete_file' in request_data:
        for file in request_data['delete_file']:
            if exists(join(WWW.config['UPLOAD_FOLDER'], file)):
                index = find_by_attribute(CONFIG['urls'], 'url', 'file:' + file)
                if index == CURRENT_ASSET:
                    NEXT_CHANGE_TIME = 0
                CONFIG['urls'].pop(index)
                remove(join(WWW.config['UPLOAD_FOLDER'], file))
        save_config()

    elif 'goto' in request_data:
        webview_goto(request_data['goto'], force=True)

    elif 'reorder' in request_data:
        CONFIG['urls'].insert(request_data['to'], CONFIG['urls'].pop(request_data['reorder']))
        save_config()
        if CURRENT_ASSET == request_data['to'] or CURRENT_ASSET == request_data['reorder']:
            webview_goto()

    elif 'back' in request_data:
        webview_goto(CURRENT_ASSET-1, backwards=True)

    elif 'refresh' in request_data:
        webview_goto()

    elif 'next' in request_data:
        webview_goto(CURRENT_ASSET+1)

    elif 'set_def_duration' in request_data:
        CONFIG['default_duration'] = request_data['set_def_duration']
        save_config()

    elif 'update' in request_data:
        if request_data.get('update_count', False):
            response['extra'] = u.get_upd_count()
        elif request_data.get('get_status', False):
            if APT_THREAD.is_alive():
                response['extra'] = APT_THREAD.name.removesuffix('e') + 'ing'
            else:
                response['extra'] = 'Idle'
        elif not APT_THREAD.is_alive():
            APT_THREAD = Thread(
                target=(u.apt_upgrade if request_data.get('do_upgrade', False) else u.apt_update),
                name=('upgrade' if request_data.get('do_upgrade', False) else 'update')
            )
            APT_THREAD.start()

    elif 'set_passwd' in request_data:
        CONFIG['users'][username]['pass'] = generate_password_hash(request_data['set_passwd'])
        save_config()

    elif 'audio_out' in request_data:
        # set_audio_device(request_data['audio_out'])
        pass

    elif 'set_hostname' in request_data:
        u.set_hostname(request_data['set_hostname'])

    elif 'get_wifis' in request_data:
        response['extra'] = u.get_wifis()

    elif 'set_netplan_conf' in request_data:
        if request_data['set_netplan_conf']:
            res = u.set_netplan(secure_filename(request_data['set_netplan_conf']), request_data['content'], request_data.get('apply', True))
        else:
            res = u.generate_netplan(request_data.get('apply', True))
        if res == True:
            if DEFAULT_AP and u.do_ip_addr(True): #AP is still enabled but now we are connected, AP is no longer needed
                u.stop_hostpot()
                IS_FIRST_BOOT = False
                NEXT_CHANGE_TIME = 0
                webview_goto(0)
        elif isinstance(res, str):
            response = {'error': 'Netplan error', 'extra': res}

    elif 'get_netplan_conf' in request_data:
        netplan_files = u.get_netplan_file_list()
        if request_data['get_netplan_conf']:
            if request_data['get_netplan_conf'] in netplan_files:
                response['extra'] = {request_data['get_netplan_conf']: u.get_netplan_file(request_data['get_netplan_conf'])}
        else:
            response['extra'] = {f: u.get_netplan_file(f) for f in netplan_files}

    elif 'new_netplan_conf' in request_data:
        u.create_netplan(request_data['new_netplan_conf'])

    elif 'del_netplan_conf' in request_data:
        res = u.del_netplan_file(request_data['del_netplan_conf'], request_data.get('apply', True))
        if isinstance(res, str):
            response = {'error': 'Netplan error', 'extra': res}

    elif 'schedule' in request_data:
        if request_data['schedule'] in ['pwr', 'reb'] and 'm' in request_data and 'h' in request_data and 'dom' in request_data and 'mon' in request_data and 'dow' in request_data:
            with CronTab(user='root') as crontab:
                crontab.new('/usr/sbin/' + ('poweroff' if request_data['schedule'] == 'pwr' else 'reboot'), 'unitotem:-)' + str(uuid4())).setall(' '.join([request_data['m'],request_data['h'],request_data['dom'],request_data['mon'],request_data['dow']]))

    elif 'set_job_state' in request_data:
        with CronTab(user='root') as crontab:
            list(crontab.find_comment(request_data['job']))[0].enable(request_data['set_job_state'])

    elif 'remove_schedule' in request_data:
        with CronTab(user='root') as crontab:
            crontab.remove_all(comment=request_data['remove_schedule'])

    elif 'edit_schedule' in request_data:
        with CronTab(user='root') as crontab:
            job = list(crontab.find_comment(request_data['edit_schedule']))[0]
            if 'm' in request_data and 'h' in request_data and 'dom' in request_data and 'mon' in request_data and 'dow' in request_data:
                job.setall(' '.join(request_data['m'],request_data['h'],request_data['dom'],request_data['mon'],request_data['dow']))
            if request_data.get('cmd') == 'pwr':
                job.set_command('/usr/sbin/poweroff')
            elif request_data.get('cmd') == 'reb':
                job.set_command('/usr/sbin/reboot')

    else:
        response = {'error': 'Invalid command', 'extra': request_data}

    return response
































@WWW.get("/backup")
def create_backup(include_uploaded: bool = False, username: str = Depends(get_current_username)):
    global VERSION, CONFIG, UPLOAD_FOLDER
    config_backup = {
        "version": VERSION,
        "CONFIG": CONFIG,
        "hostname": u.get_hostname(),
        # "def_audio_dev": get_default_audio_device(),
        "netplan": {fname: u.get_netplan_file(fname) for fname in u.get_netplan_file_list()}
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
def create_backup(backup_file: UploadFile, options: dict[str, bool], username: str = Depends(get_current_username)):
    global CONFIG, IS_FIRST_BOOT,CURRENT_ASSET, NEXT_CHANGE_TIME, UPLOAD_FOLDER
    if backup_file.content_type not in ['application/octet-stream', 'application/zip']:
        with ZipFile(backup_file.stream._file) as zip_file:
            files = zip_file.namelist()
            if 'config.json' in files:
                config_json = loads(zip_file.read('config.json'))
                if 'CONFIG' in config_json and options.get('CONFIG', True):
                    CONFIG = config_json['CONFIG']
                    CURRENT_ASSET    = -1
                    IS_FIRST_BOOT    = True
                    NEXT_CHANGE_TIME = 0
                    save_config()
                if 'hostname' in config_json and options.get('hostname', True):
                    u.set_hostname(config_json['hostname'])
                if 'def_audio_dev' in config_json and options.get('def_audio_dev', True):
                    # set_audio_device(config_json['def_audio_dev'])
                    pass
                if 'netplan' in config_json and options.get('netplan', True):
                    res = u.set_netplan(filename = None, file_content=config_json['netplan'])
            if options.get('uploaded', True):
                for file in files:
                    if file.startswith('uploaded/'):
                        zip_file.extract(file, normpath(join(UPLOAD_FOLDER, '..')))
            if 'res' in locals():
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=res)
    else:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail='Wrong mimetype: ' + backup_file.content_type)
            
@WWW.delete('/backup', status_code=status.HTTP_204_NO_CONTENT)
def factory_reset(username: str = Depends(get_current_username)):
    global CFG_FILE, CONFIG, CONFIG_DEF, CURRENT_ASSET, IS_FIRST_BOOT, NEXT_CHANGE_TIME
    if isfile(CFG_FILE): remove(CFG_FILE)
    # if isfile(ASOUND_CONF): remove(ASOUND_CONF)
    for res in list_resources(): remove(join(UPLOAD_FOLDER, res))
    CONFIG           = copy(CONFIG_DEF)
    CURRENT_ASSET    = -1
    IS_FIRST_BOOT    = True
    NEXT_CHANGE_TIME = 0


# @WWW.post("/api/power/reboot")
# def reboot(response: Response, username: str = Depends(get_current_username)):
#     run('/usr/sbin/reboot')

# @WWW.post("/api/power/shutdown")
# def shutdown(response: Response, username: str = Depends(get_current_username)):
#     run('/usr/sbin/poweroff')

@WWW.post("/api/scheduler/asset")
def add_asset(response: Response, items: dict[str, dict[str, int|bool]], username: str = Depends(get_current_username)):
    global CONFIG
    save = False
    invalid = []
    for element, attrs in items.items():
        if is_valid_url(element) or element in list_resources():
            CONFIG['urls'].append({
                'url': ('file:' if element in list_resources() else '') + element,
                'duration': int(attrs.get('duration', '') or CONFIG['default_duration']),
                'enabled': bool(attrs.get('enabled', False))
            })
            save = True
        else: invalid.append(element)
    if save: save_config()
    if invalid:
        raise HTTPException(status_code=status.HTTP_406_NOT_ACCEPTABLE, detail='Invalid elements: ' + str(invalid))

# @WWW.patch("/api/scheduler/set_state")
# def set_state(response: Response, url: str, state: bool, username: str = Depends(get_current_username)):
#     global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG
#     for index, elem in enumerate(CONFIG['urls']):
#         if url == elem['url']:
#             start_rotation = not enabled_asset_count()
#             CONFIG['urls'][index]['enabled'] = state
#             save_config()
#             if start_rotation:
#                 webview_goto(0)
#             elif index == CURRENT_ASSET:
#                 NEXT_CHANGE_TIME = 0
#             break

# @WWW.post("/api/scheduler/update_duration")
# def update_duration(response: Response, url: str, duration: int, username: str = Depends(get_current_username)):
#     global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG
#     for index, elem in enumerate(CONFIG['urls']):
#         if url == elem['url']:
#             old_dur = CONFIG['urls'][index]['duration']
#             CONFIG['urls'][index]['duration'] = duration
#             save_config()
#             if index == CURRENT_ASSET:
#                 NEXT_CHANGE_TIME += (CONFIG['urls'][index]['duration'] or float('inf')) - old_dur
#             break

# @WWW.post("/api/scheduler/delete")
# def delete(response: Response, url: str, username: str = Depends(get_current_username)):
#     global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG
#     for elem_n in range(len(CONFIG['urls'])):
#         if url == CONFIG['urls'][elem_n]['url']:
#             try:
#                 if CONFIG['urls'][CURRENT_ASSET]['url'] == CONFIG['urls'][elem_n]['url']:
#                     NEXT_CHANGE_TIME = 0
#             finally:
#                 CONFIG['urls'].pop(elem_n)
#                 save_config()
#                 break

# @WWW.post("/api/scheduler/upload")
# def media_upload(response: Response, files: list[UploadFile] | None = list(), username: str = Depends(get_current_username)):
#     global UPLOAD_FOLDER
#     for file in files:
#         if file and file.filename:
#             file.save(join(UPLOAD_FOLDER, secure_filename(file.filename)), buffer_size=64 * 1024 * 1024)
#             file_data = get_resources(secure_filename(file.filename))
#             add_asset(response, {file_data['filename']: {'duration': file_data['duration_s']}}, username)
#     response.status_code = status.HTTP_201_CREATED

# @WWW.post("/api/scheduler/delete_file")
# def delete_file(response: Response, files: list[str], username: str = Depends(get_current_username)):
#     global UPLOAD_FOLDER
#     for file in files:
#         if exists(join(UPLOAD_FOLDER, file)):
#             delete('file:' + file)
#             remove(join(UPLOAD_FOLDER, file))

# @WWW.post("/api/scheduler/go_to")
# def go_to_asset_number(response: Response, index: int = CURRENT_ASSET, force: bool = True, username: str = Depends(get_current_username)):
#     webview_goto(index, force)

# @WWW.post("/api/scheduler/go_to/{dir}")
# def refresh_back_next(response: Response, dir: str, username: str = Depends(get_current_username)):
#     global CURRENT_ASSET
#     webview_goto(CURRENT_ASSET + (1 if dir=='next' else -1 if dir=='back' else 0), backwards=dir=='back')

# @WWW.post("/api/scheduler/reorder")
# def reorder(response: Response, from_i: int, to_i: int, username: str = Depends(get_current_username)):
#     global CURRENT_ASSET, CONFIG
#     CONFIG['urls'].insert(to_i, CONFIG['urls'].pop(from_i))
#     save_config()
#     if CURRENT_ASSET == to_i or CURRENT_ASSET == from_i:
#         webview_goto()

# @WWW.post("/api/settings/set_default_duration")
# def set_default_duration(response: Response, duration: int, username: str = Depends(get_current_username)):
#     global CONFIG
#     CONFIG['default_duration'] = duration
#     save_config()

# @WWW.post("/api/settings/update")
# def apt_control(response: Response, get_count: bool = False, get_status: bool = False, do_upgrade: bool = False, username: str = Depends(get_current_username)):
#     global APT_THREAD
#     if get_count:
#         return u.get_upd_count()
#     elif get_status and APT_THREAD.is_alive():
#         return APT_THREAD.name.removesuffix('e') + 'ing'
#     elif not APT_THREAD.is_alive():
#         APT_THREAD = Thread(
#             target=(u.apt_upgrade if do_upgrade else u.apt_update),
#             name=('upgrade' if do_upgrade else 'update')
#         )
#         APT_THREAD.start()

# @WWW.post("/api/settings/set_passwd")
# def set_passwd(response: Response, new_pass: str, username: str = Depends(get_current_username)):
#     global CONFIG
#     CONFIG['users'][AUTH.current_user()]['pass'] = generate_password_hash(new_pass)
#     save_config()

# @WWW.post("/api/settings/audio/set_default")
# def set_default_audio_device(response: Response, name: str, username: str = Depends(get_current_username)):
#     pass

# @WWW.post("/api/settings/audio/get_default")
# def get_default_audio_device(response: Response, username: str = Depends(get_current_username)):
#     pass

# @WWW.post("/api/settings/audio/get_devices")
# def get_audio_devices(response: Response, username: str = Depends(get_current_username)):
#     pass

# @WWW.post("/api/settings/audio/get_volume")
# def get_volume(response: Response, device: str | None = None, username: str = Depends(get_current_username)):
#     pass

# @WWW.post("/api/settings/audio/set_volume")
# def set_volume(response: Response, volume: float, device: str | None = None, username: str = Depends(get_current_username)):
#     pass

# @WWW.post("/api/settings/set_hostname")
# def set_hostname(response: Response, hostname: str, username: str = Depends(get_current_username)):
#     u.set_hostname(hostname)
    
# @WWW.post("/api/settings/get_wifis")
# def get_wifis(response: Response, username: str = Depends(get_current_username)):
#     return u.get_wifis()

# @WWW.post("/api/settings/netplan/set_conf")
# def set_conf(response: Response, filename: str | None = None, content: str | None = None, apply: bool = True, username: str = Depends(get_current_username)):
#     global NEXT_CHANGE_TIME, IS_FIRST_BOOT
#     if filename:
#         res = u.set_netplan(secure_filename(filename), content, apply)
#     else:
#         res = u.generate_netplan(apply)
#     if res == True:
#         if DEFAULT_AP and u.do_ip_addr(True): #AP is still enabled but now we are connected, AP is no longer needed
#             u.stop_hostpot()
#             IS_FIRST_BOOT = False
#             NEXT_CHANGE_TIME = 0
#             webview_goto(0)
#     elif isinstance(res, str):
#         response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
#         return res

# @WWW.post("/api/settings/netplan/get_conf")
# def get_conf(response: Response, filename: str | None = None, username: str = Depends(get_current_username)):
#     netplan_files = u.get_netplan_file_list()
#     if filename:
#         if filename in netplan_files:
#             return {filename: u.get_netplan_file(filename)}
#     else:
#         return {f: u.get_netplan_file(f) for f in netplan_files}

# @WWW.post("/api/settings/netplan/new_conf")
# def new_conf(response: Response, filename: str | None = None, username: str = Depends(get_current_username)):
#     u.create_netplan(filename)

# @WWW.post("/api/settings/netplan/del_conf")
# def del_conf(response: Response, filename: str | None = None, apply: bool = True, username: str = Depends(get_current_username)):
#     res = u.del_netplan_file(filename, apply)
#     if isinstance(res, str):
#         response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
#         return res

# @WWW.post("/api/settings/cron/add_job")
# def add_job(response: Response, action: str, minute: int|str, hour: int|str, month_day: int|str, month: int|str, week_day: int|str, username: str = Depends(get_current_username)):
#     with CronTab(user='root') as crontab:
#         crontab.new('/usr/sbin/' + ('poweroff' if action == 'pwr' else 'reboot'), 'unitotem:-)' + str(uuid4())) \
#             .setall(' '.join([minute, hour, month_day, month, week_day]))

# @WWW.post("/api/settings/cron/set_job_state")
# def set_job_state(response: Response, job_id: str, enabled: bool, username: str = Depends(get_current_username)):
#     with CronTab(user='root') as crontab:
#         list(crontab.find_comment(job_id))[0].enable(enabled)

# @WWW.post("/api/settings/cron/delete_job")
# def delete_job(response: Response, job_id: str, username: str = Depends(get_current_username)):
#     with CronTab(user='root') as crontab:
#         crontab.remove_all(comment=job_id)

# @WWW.post("/api/settings/cron/edit_job")
# def edit_job(response: Response, job_id: str, action: str|None = None, minute: int|str|None = None, hour: int|str|None = None, month_day: int|str|None = None, month: int|str|None = None, week_day: int|str|None = None, username: str = Depends(get_current_username)):
#     with CronTab(user='root') as crontab:
#         job = list(crontab.find_comment(job_id))[0]
#         if minute and hour and month_day and month and week_day:
#             job.setall(' '.join(minute,hour,month_day,month,week_day))
#         if action == 'pwr':
#             job.set_command('/usr/sbin/poweroff')
#         elif action == 'reb':
#             job.set_command('/usr/sbin/reboot')










@WWW.get("/unitotem-no-assets", response_class=HTMLResponse)
def no_assets_page(request: Request):
    ip = u.do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('no-assets.html', dict(
        request=request,
        ut_vers=VERSION,
        os_vers=OS_VERSION,
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=u.get_hostname()
    ))

@WWW.get("/unitotem-first-boot", response_class=HTMLResponse)
def first_boot_page(request: Request):
    DEFAULT_AP = dict(ssid='ssid', password = 'pwd00', qrcode = u.wifi_qr('ssid', 'pwd'))
    ip = u.do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('first-boot.html', dict(
        request=request,
        ut_vers=VERSION,
        os_vers=OS_VERSION,
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=u.get_hostname(),
        wifi=DEFAULT_AP
    ))


def webview_goto(asset: int = CURRENT_ASSET, force: bool = False, backwards: bool = False):
    global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG
    if 0 <= asset < len(CONFIG['urls']):
        if CONFIG['urls'][asset]['enabled'] or force:
            CURRENT_ASSET = asset
            url:str = CONFIG['urls'][CURRENT_ASSET]['url']
            if url.startswith('file:'):
                url = 'http://localhost:5000/static/uploaded/' + url.removeprefix('file:')
            # UI_BUS.Show(url)
            NEXT_CHANGE_TIME = int(time()) + (CONFIG['urls'][CURRENT_ASSET]['duration'] or float('inf'))
        else:
            webview_goto(asset + (-1 if backwards else 1))

def webview_control_main():
    global NEXT_CHANGE_TIME, CURRENT_ASSET, CONFIG
    while(True):
        if time()>=NEXT_CHANGE_TIME:
            if IS_FIRST_BOOT:
                NEXT_CHANGE_TIME = float('inf')
                # UI_BUS.Show('https://localhost/unitotem-first-boot')
            elif not enabled_asset_count():
                NEXT_CHANGE_TIME = float('inf')
                # UI_BUS.Show('https://localhost/unitotem-no-assets')
            else:
                CURRENT_ASSET += 1
                if CURRENT_ASSET >= len(CONFIG['urls']): CURRENT_ASSET = 0
                webview_goto(CURRENT_ASSET)
        sleep(1)


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('--no-gui', action='store_true', help='Start UniTotem Manager without webview gui (for testing)')
    parser.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    cmdargs = vars(parser.parse_args())


    # makedirs(UPLOADED_FOLDER, exist_ok=True)
    makedirs(CFG_DIR, exist_ok=True)
    
    IS_FIRST_BOOT = not load_config()

    if IS_FIRST_BOOT:
        print('First boot or no configuration file found.')

    if IS_FIRST_BOOT and (not u.do_ip_addr(True) or exists(u.FALLBACK_AP_FILE)):
        # config file doesn't exist and we are not connected, maybe it's first boot
        hotspot = u.start_hotspot()
        DEFAULT_AP = dict(ssid=hotspot[0], password = hotspot[1], qrcode = u.wifi_qr(hotspot[0], hotspot[1]))
        print(f'Not connected to any network, started fallback hotspot {hotspot[0]} with password {hotspot[1]}.')

    if not APT_THREAD.is_alive():
        APT_THREAD = Thread(target=u.apt_update, name='update')
        APT_THREAD.start()

    if not cmdargs.get('no_gui', False):
        Thread(target=webview_control_main, daemon=True).start()
    Thread(target=lambda:uvicorn_run(WWW, port=443, ssl_keyfile="/etc/ssl/unitotem_key.pem", ssl_certfile="/etc/ssl/unitotem_crt.pem")).start()

    # redirect = FastAPI()
    # redirect.add_middleware(HTTPSRedirectMiddleware)
    # Thread(target=lambda:uvicorn_run(app=redirect, port=80)).start()
    # app = FastAPI()
    # Thread(target=uvicorn_run(app, port=443, ssl_keyfile="/etc/ssl/unitotem_key.pem", ssl_certfile="/etc/ssl/unitotem_crt.pem")).start()



    u.stop_hostpot()