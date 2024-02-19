import asyncio
import base64
import signal
import warnings
from argparse import ArgumentParser
from ipaddress import IPv4Address
from json import loads
from os import environ
from os.path import exists
from platform import freedesktop_os_release as os_release
from platform import node as get_hostname
from traceback import print_exc
from typing import Any, Literal, Union, cast

import asyncwebsockets
import requests
import urllib3
import uvloop
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import (Depends, FastAPI, Request, UploadFile, status)
from fastapi.middleware import Middleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import HTMLResponse
from fastapi.routing import Mount
from fastapi.staticfiles import StaticFiles
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from jwt import InvalidSignatureError
from natsort import natsorted
from psutil import (cpu_count, sensors_battery, sensors_fans,
                    sensors_temperatures, virtual_memory)
from pydantic import PositiveInt
from watchdog.observers import Observer
from wsproto.events import CloseConnection

from utils import *

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)


SHUTDOWN_EVENT = asyncio.Event()



# noinspection PyTypeChecker
WWW = FastAPI(
    title='UniTotem', version=const.__version__,
    middleware=[Middleware(HTTPSRedirectMiddleware)],
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
WWW.include_router(ws_endpoints_router)


@WWW.post("/api/scheduler/upload", status_code=status.HTTP_201_CREATED, dependencies=[Depends(LOGMAN)])
async def media_upload(files: list[UploadFile]):
    for infile in files:
        await UPLOADS.save(infile)





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
        def_wifi=get_ifaces(IF_WIRELESS)[0]
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


    async def info_loop(_ws: WSManager, waiter: asyncio.Event):
        while not waiter.is_set():
            await broadcast_sysinfo(_ws)
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
