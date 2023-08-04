__author__ = 'Alessandro Campolo (a13ssandr0)'
__version__ = '3.0.0'


import asyncio
import signal
from argparse import ArgumentParser
from datetime import datetime
from io import BytesIO
from json import dumps, loads
from math import inf
from os.path import dirname, exists, join
from pathlib import Path
from platform import freedesktop_os_release as os_release
from platform import node as get_hostname
from subprocess import run as cmd_run
from threading import Thread
from time import strftime, time
from traceback import format_exc, print_exc
from typing import Annotated, Any, Optional, Union
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import uvloop
from fastapi import (Body, Depends, FastAPI, HTTPException, Request, Response,
                     UploadFile, WebSocket, WebSocketDisconnect, status)
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
from utils import *
from validators import url as is_valid_url
from watchdog.observers import Observer
from werkzeug.utils import secure_filename

APT_THREAD           = Thread(target=apt_update, name='update')

DEF_WIFI_CARD        = get_ifaces(IF_WIRELESS)[0]
DEFAULT_AP           = None

DISPLAYS:list[dict]  = []
WINDOW               = {'bounds': {}, 'orientation':-2, 'flip':-2}

UI_WS                = WSManager(True)
WS                   = WSManager()

TEMPLATES            = Jinja2Templates(directory=join(dirname(__file__), 'templates'), extensions=['jinja2.ext.do'])
TEMPLATES.env.filters['flatten'] = flatten
UPLOADS              = UploadManager(Path(__file__).parent.joinpath('uploaded'), lambda x: WS.broadcast('scheduler/file', files=x))

WWW = FastAPI(
    title='UniTotem', version=__version__,
    middleware=[Middleware(HTTPSRedirectMiddleware)],
    routes=[
        Mount('/static', StaticFiles(directory=join(dirname(__file__), 'static')), name='static'),
        Mount('/uploaded', StaticFiles(directory=UPLOADS.folder), name='uploaded')
    ],
    exception_handlers={
        InvalidSignatureError: login_redir,
        NotAuthenticatedException: login_redir
    }
)
WWW.include_router(login_router)




@WWW.websocket("/ui_ws")
async def ui_websocket(websocket: WebSocket):
    global DISPLAYS, WINDOW
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
        try:
            data:dict[str,Any] = await websocket.receive_json()
            t = data.pop('target')
            try:
                await WS.handlers[t](websocket, **data)
            except KeyError:
                await WS.send(websocket, 'error', error='Invalid command', extra=dumps({'target':t, **data}, indent=4))

        except WebSocketDisconnect:
            break
        except Exception:
            await WS.send(websocket, 'error', error='Exception', extra=format_exc())
            print_exc()
    WS.disconnect(websocket)




WS.add('power/reboot')(lambda: cmd_run('/usr/sbin/reboot'))

WS.add('power/shutdown')(lambda: cmd_run('/usr/sbin/poweroff'))

WS.add('scheduler/asset')(lambda: WS.broadcast('scheduler/asset', items=Config.assets.serialize(), current=Config.assets.current.uuid))

@WS.add('scheduler/add_url')
async def add_url(ws: WebSocket, items:list[str|dict] = []):
    invalid = []
    for element in items:
        if isinstance(element, str):
            element = {'url': element}
        element.pop('uuid', None) # uuid MUST be generated internally
        if is_valid_url(element['url']): # type: ignore
            Config.assets.append(element)
        else: invalid.append(element)
    Config.save()
    if invalid:
        await WS.send(ws, 'scheduler/add_url', error='Invalid elements', extra=invalid)
    
@WS.add('scheduler/add_file')
async def add_file(ws: WebSocket, items: list[str|dict] = []):
    invalid = []
    for element in items:
        if isinstance(element, str):
            element = {'url': element}
        if element['url'] in UPLOADS.filenames: # type: ignore
            Config.assets.append({
                'url': 'file:' + element['url'],
                'duration': element.get('duration', UPLOADS.files_info[element['url']].duration_s),
                'enabled': element.get('enabled', False),
            })
        else: invalid.append(element)
    Config.save()
    if invalid:
        await WS.send(ws, 'scheduler/add_file', error='Invalid elements', extra=invalid)

@WS.add('scheduler/asset/edit')
async def asset_edit(uuid: str,
                    name:Optional[str] = None,
                    url:Optional[str] = None,
                    duration:Optional[Union[int,float]] = None,
                    ena_date:Optional[datetime] = None,
                    dis_date:Optional[datetime] = None,
                    state:Optional[bool] = None):
    asset = Config.assets[uuid]
    if name != None:
        asset.name = name
    if url != None:
        asset.url = url
    if duration != None:
        duration, asset.duration = asset.duration, duration
        if asset == Config.assets.current:
            Config.assets.next_change_time += (asset.duration or inf) - duration # type: ignore
    if ena_date != None:
        asset.ena_date = ena_date
    if dis_date != None:
        asset.dis_date = dis_date
    if state != None:
        start_rotation = not Config.enabled_asset_count
        asset.enabled = state
        if start_rotation:
            await webview_goto(0)
        elif asset == Config.assets.current:
            Config.assets.next_change_time = 0
    Config.save()

@WS.add('scheduler/validate_url')
async def url_validate(ws: WebSocket, url: str):
    await WS.send(ws, 'scheduler/validate_url', valid=bool(is_valid_url(url))) # type: ignore

@WS.add('scheduler/asset/current')
async def asset_current():
    if Config.assets.currentid >= 0:
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
async def playlist_goto(index: int = Config.assets.currentid, force: bool = False):
    await webview_goto(index, True)

WS.add('scheduler/goto/back')(lambda: webview_goto(Config.assets.currentid-1, backwards=True))

WS.add('scheduler/goto/next')(lambda: webview_goto(Config.assets.currentid+1))

@WS.add('scheduler/reorder')
async def playlist_reorder(from_i: int, to_i: int):
    Config.assets.move(from_i, to_i)
    Config.save()
    if Config.assets.currentid in [from_i, to_i]:
        await webview_goto()  #show new asset

@WS.add('settings/default_duration')
async def settings_default_duration(duration: Optional[int] = None):
    if duration != None:
        Config.def_duration = duration
        Config.save()
    await WS.broadcast('settings/default_duration', duration=Config.def_duration)

@WS.add('settings/update')
async def settings_update(do_upgrade: bool = False):
    global APT_THREAD
    if not APT_THREAD.is_alive():
        loop = asyncio.get_running_loop()
        def apt():
            for line in apt_update(do_upgrade):
                if line == True:
                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/start', upgrading=do_upgrade), loop)
                elif line == False:
                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/end', upgrading=do_upgrade), loop)
                elif isinstance(line, tuple):
                    asyncio.run_coroutine_threadsafe(WS.broadcast('settings/update/progress', upgrading=do_upgrade, is_stdout=line[0], data=line[1]), loop)

        APT_THREAD = Thread(target=apt, name=('upgrade' if do_upgrade else 'update'))
        APT_THREAD.start()
        await WS.broadcast('settings/update/status', status=APT_THREAD.name, log=get_apt_log())

WS.add('settings/update/list')(lambda: WS.broadcast('settings/update/list', updates=apt_list_upgrades()))

WS.add('settings/update/reboot_required')(lambda: WS.broadcast('settings/update/reboot_required', reboot=reboot_required()))
        
WS.add('settings/update/status')(lambda: WS.broadcast('settings/update/status', status=APT_THREAD.name if APT_THREAD.is_alive() else None, log=get_apt_log()))

@WS.add('settings/audio/default')
async def settings_audio_default(device: Optional[str] = None):
    if device != None:
        setDefaultAudioDevice(device)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

WS.add('settings/audio/devices')(lambda: WS.broadcast('settings/audio/devices', devices=getAudioDevices()))

@WS.add('settings/audio/volume')
async def settings_audio_volume(device: Optional[str] = None, volume: Optional[int] = None):
    if volume != None:
        setVolume(device, volume)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

@WS.add('settings/audio/mute')
async def settings_audio_mute(device: Optional[str] = None, mute: Optional[bool] = None):
    if mute != None:
        setMute(device, mute)
    await WS.broadcast('settings/audio/devices', devices=getAudioDevices())

WS.add('settings/display/getBounds')(lambda: WS.broadcast('settings/display/getBounds', **WINDOW['bounds']))

@WS.add('settings/display/setBounds')
async def settings_display_boundsset(x: int, y: int, w: int, h: int):
    await UI_WS.broadcast('setBounds', x=x, y=y, width=w, height=h)

WS.add('settings/display/getOrientation')(lambda: WS.broadcast('settings/display/getOrientation', orientation=WINDOW['orientation']))

@WS.add('settings/display/setOrientation')
async def settings_display_orientationset(orientation: int):
    await UI_WS.broadcast('setOrientation', orientation=orientation)

WS.add('settings/display/getFlip')(lambda: WS.broadcast('settings/display/getFlip', flip=WINDOW['flip']))

@WS.add('settings/display/setFlip')
async def settings_display_flipset(flip: int):
    await UI_WS.broadcast('setFlip', flip=flip)

@WS.add('settings/hostname')
async def settings_hostname(hostname: Optional[str] = None):
    if hostname != None:
        set_hostname(hostname)
    await WS.broadcast('settings/hostname', hostname=get_hostname())

WS.add('settings/get_wifis')(lambda: WS.broadcast('settings/get_wifis', wifis=get_wifis()))

@WS.add('settings/netplan/file/new')
async def settings_netpfile_new(filename: str):
    create_netplan(filename)
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

@WS.add('settings/netplan/file/set')
async def settings_netpfile_set(ws: WebSocket, filename: Optional[str] = None, content: str = '', apply: bool = True):
    if filename != None:
        res = set_netplan(secure_filename(filename), content, apply)
    else:
        res = generate_netplan(apply)
    if res == True:
        if DEFAULT_AP and do_ip_addr(True): #AP is still enabled but now we are connected, AP is no longer needed
            stop_hostpot()
            Config.assets.next_change_time = 0
            await webview_goto(0)
    elif isinstance(res, str):
        await WS.send(ws, 'error', error='Netplan error', extra=res)
    await WS.broadcast('settings/netplan/file/get', files={f: get_netplan_file(f) for f in get_netplan_file_list()})

@WS.add('settings/netplan/file/get')
async def settings_netpfile_get(filename: Optional[str] = None):
    netplan_files = get_netplan_file_list()
    if  filename != None:
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
async def settings_cronjob_add(cmd: str = '', m = None, h = None, dom = None, mon = None, dow = None):
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
async def settings_cronjob_edit(ws: WebSocket, job: str, cmd: str = '', m = None, h = None, dom = None, mon = None, dow = None):
    CRONTAB.read()
    try:
        _job = next(CRONTAB.find_comment(job))
        if all([x != None for x in [m, h, dom, mon, dow]]):
            _job.setall(m,h,dom,mon,dow)
        if cmd == 'pwr':
            _job.set_command('/usr/sbin/poweroff')
        elif cmd == 'reb':
            _job.set_command('/usr/sbin/reboot')
        CRONTAB.write()
    except StopIteration:
        await WS.send(ws, 'error', error='Not found', extra='Requested job does not exist')
    await WS.broadcast('settings/cron/job', jobs=CRONTAB.serialize())






@WWW.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password:str, username: str = Depends(LOGMAN)):
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
        "version": __version__,
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
        headers={'Content-Disposition': 'attachment; filename="' + strftime('unitotem-manager-%Y%m%d-%H%M%S.zip') + '"'})

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
                        #with version 3.0.0 audio controls changed from alsa to pulseaudio
                        setDefaultAudioDevice(config_json['def_audio_dev'])

                if 'netplan' in config_json and netplan:
                    set_netplan(filename = None, file_content=config_json['netplan'], apply=False)

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
    CRONTAB.remove_all(comment=CRONTAB._cron_re)
    CRONTAB.write()

    await UI_WS.broadcast('reset', nocache=True)
    
    for file in UPLOADS.files: UPLOADS.remove(file)


@WWW.get("/", response_class=HTMLResponse)
async def scheduler(request: Request, username:str = Depends(LOGMAN)):
    return TEMPLATES.TemplateResponse('index.html.j2', dict(
        request=request,
        ut_vers=__version__,
        logged_user=username,
        hostname=get_hostname(),
        disp_size=WINDOW['bounds'],
        disk_used=UPLOADS.disk_usedh,  # type: ignore
        disk_total=UPLOADS.disk_totalh # type: ignore
    ))

@WWW.get('/login')
async def login_page(request: Request, src:Optional[str] = '/'):
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
        ut_vers=__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname()
    ))

@WWW.get("/settings", response_class=HTMLResponse)
@WWW.get("/settings/{tab}", response_class=HTMLResponse)
async def settings(request: Request, tab: str = 'main_menu', username: str = Depends(LOGMAN)):
    data:dict[str, Any] = dict(
        request=request,
        ut_vers=__version__,
        logged_user=username,
        cur_tab=tab,
        disp_size=WINDOW['bounds'],
        disk_used=UPLOADS.disk_usedh,   # type: ignore
        disk_total=UPLOADS.disk_totalh, # type: ignore
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
        data['has_battery'] = sensors_battery() != None
        data['temp_devs'] = {k:[x._asdict() for x in natsorted(v, key=lambda x: x.label)] for k,v in sensors_temperatures().items()}
        data['fan_devs'] = {k:[x._asdict() for x in v] for k,v in sensors_fans().items()}
    
    return TEMPLATES.TemplateResponse(f'settings/{tab}.html.j2', data)

@WWW.api_route("/unitotem-no-assets", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def no_assets_page(request: Request):
    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('no-assets.html.j2', dict(
        request=request,
        ut_vers=__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname()
    ))

@WWW.api_route("/unitotem-first-boot", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def first_boot_page(request: Request):
    ip = do_ip_addr(get_default=True)
    return TEMPLATES.TemplateResponse('first-boot.html.j2', dict(
        request=request,
        ut_vers=__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname(),
        wifi=DEFAULT_AP
    ))

async def webview_goto(asset: int = Config.assets.currentid, force: bool = False, backwards: bool = False):
    if 0 <= asset < len(Config.assets):
        if Config.assets[asset].enabled or force:
            Config.assets.currentid = asset
            url = Config.assets.current.url
            if url.startswith('file:'):
                url = 'https://localhost/uploaded/' + url.removeprefix('file:')
            await UI_WS.broadcast('Show', src=url)
            Config.assets.next_change_time = int(time()) + (Config.assets.current.duration or inf)
            await WS.broadcast('scheduler/asset/current', uuid=Config.assets.current.uuid)
        else:
            await webview_goto(asset + (-1 if backwards else 1))

async def webview_control_main(waiter: asyncio.Event):
    while not waiter.is_set():
        if time()>=Config.assets.next_change_time:
            if Config.first_boot:
                Config.assets.next_change_time = inf
                Config.assets.currentid = -1
                await WS.broadcast('scheduler/asset/current', uuid=None)
                await UI_WS.broadcast('Show', src='https://localhost/unitotem-first-boot', container='web')
            elif not Config.enabled_asset_count:
                Config.assets.next_change_time = inf
                Config.assets.currentid = -1
                await WS.broadcast('scheduler/asset/current', uuid=None)
                await UI_WS.broadcast('Show', src='https://localhost/unitotem-no-assets', container='web')
            else:
                Config.assets.currentid += 1
                if Config.assets.currentid >= len(Config.assets): Config.assets.currentid = 0
                await webview_goto(Config.assets.currentid)
        await asyncio.sleep(1)



if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument('--no-gui', action='store_true', help='Start UniTotem Manager without webview gui (for testing)')
    parser.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    cmdargs = vars(parser.parse_args())


    try:
        Config(filename='/etc/unitotem/unitotem.conf')
    except FileNotFoundError:
        print('First boot or no configuration file found.')
        try:
            if (not do_ip_addr(True) or exists(FALLBACK_AP_FILE)):
                # config file doesn't exist and we are not connected, maybe it's first boot
                hotspot = start_hotspot()
                DEFAULT_AP = dict(ssid=hotspot[0], password = hotspot[1], qrcode = wifi_qr(hotspot[0], hotspot[1]))
                print(f'Not connected to any network, started fallback hotspot {hotspot[0]} with password {hotspot[1]}.')
        except:
            print("Couldn't start wifi hotspot.")
            print_exc()


    APT_THREAD.start()

    uvloop.install()

    shutdown_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda *_: shutdown_event.set())

    Config.assets.set_callback(lambda assets, current: WS.broadcast('scheduler/asset', items=assets, current=current), loop)

    UPLOADS._evloop = loop

    observer = Observer()
    observer.schedule(UPLOADS, UPLOADS.folder)
    observer.start()

    UPLOADS.scan_folder()

    if not cmdargs.get('no_gui', False):
        loop.create_task(webview_control_main(shutdown_event), name='page_controller')

    async def info_loop(ws: WSManager, waiter: asyncio.Event):
        while not waiter.is_set():
            await broadcast_sysinfo(ws)
            await asyncio.sleep(3)

    loop.create_task(info_loop(WS, shutdown_event), name='info_loop')

    loop.create_task(serve(WWW, HyperConfig().from_mapping({ # type: ignore
        'bind': ['0.0.0.0:443'],
        'insecure_bind': ['0.0.0.0:80'],
        'certfile': '/etc/ssl/unitotem.pem',
        'keyfile': '/etc/ssl/unitotem.pem',
        'accesslog': '-',
        'errorlog': '-',
        'loglevel': 'INFO'
    }), shutdown_trigger=shutdown_event.wait), name='server') # type: ignore

    loop.run_forever()

    stop_hostpot()

    observer.stop()
    APT_THREAD.join()