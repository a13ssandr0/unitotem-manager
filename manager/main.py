import asyncio
import signal
import warnings
from argparse import ArgumentParser
from os.path import exists
from platform import freedesktop_os_release as os_release, node as get_hostname
from traceback import format_exc
from typing import Literal, Union

import urllib3
from fastapi import (FastAPI, HTTPException, Request)
from fastapi.middleware import Middleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import HTMLResponse
from fastapi.routing import Mount
from fastapi.staticfiles import StaticFiles
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from jwt import InvalidSignatureError
from loguru import logger
from watchdog.observers import Observer

import api.constants as const
import routers
from api.commons import SHUTDOWN_EVENT, UPLOADS
from api.constants import Arguments
from api.models import Config
from api.ws.endpoints import REMOTE_WS, WS, api
from api.ws.wsmanager import WSManager
from routers.error import http_exception_handler
from routers.login import NotAuthenticatedException, login_redirect
from templates import templates
from utils.logging import Logger
from utils.system.network.hotspot import FALLBACK_AP_FILE, start_hotspot, stop_hostpot, wifi_qr, DEFAULT_AP
from utils.system.network.ip import do_ip_addr
from utils.system.sysinfo import get_sysinfo

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)

# noinspection PyTypeChecker
WWW = FastAPI(
    title='UniTotem', version=const.__version__,
    middleware=[Middleware(HTTPSRedirectMiddleware)],
    routes=[
        Mount('/static', StaticFiles(directory=const.static_folder), name='static'),
        Mount('/uploaded', StaticFiles(directory=const.uploads_folder), name='uploaded')
    ],
    exception_handlers={
        InvalidSignatureError: login_redirect,
        NotAuthenticatedException: login_redirect,
        HTTPException: http_exception_handler,
    }
)
WWW.include_router(routers.login.router)
WWW.include_router(routers.websocket.remote.router)
WWW.include_router(routers.websocket.web_ui.router)
WWW.include_router(routers.scheduler.router)
WWW.include_router(routers.settings.router)
WWW.include_router(routers.backup.router)


@WWW.api_route("/unitotem-{page}", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def first_boot_page(request: Request, page: Union[Literal['first-boot'], Literal['no-assets']]):
    ip = do_ip_addr(get_default=True)
    return templates.TemplateResponse(request, f'{page}.html.j2', dict(
        ut_vers=const.__version__,
        os_vers=os_release()['PRETTY_NAME'],
        ip_addr=ip['addr'][0]['addr'] if ip else None,
        hostname=get_hostname(),
        wifi=DEFAULT_AP
    ))


parser = ArgumentParser()
parser.add_argument('--no-gui', action='store_true',
                    help='Start UniTotem Manager without webview gui (for testing)')
parser.add_argument('--http-bind', default=const.default_bind)
parser.add_argument('--http-port', default=const.default_port)  # , gt=0, le=65525)
parser.add_argument('--https-bind', default=const.default_bind_secure)
parser.add_argument('--https-port', default=const.default_port_secure)  # , gt=0, le=65525)
parser.add_argument('--config', default=const.default_config_file)
parser.add_argument('--version', action='version', version='%(prog)s ' + const.__version__)
cmdargs = Arguments.model_validate(vars(parser.parse_args()))

try:
    Config(filename=cmdargs.config)
except FileNotFoundError:
    logger.warning('First boot or no configuration file found.')
    try:
        if not do_ip_addr(True) or exists(FALLBACK_AP_FILE):
            # config file doesn't exist, and we are not connected, maybe it's first boot
            hotspot = start_hotspot()
            DEFAULT_AP = dict(ssid=hotspot[0], password=hotspot[1], qrcode=wifi_qr(hotspot[0], hotspot[1]))
            logger.info(
                f'Not connected to any network, started fallback hotspot {hotspot[0]} with password {hotspot[1]}.')
    except Exception:
        logger.error("Couldn't start wifi hotspot.")
        logger.error(format_exc())

REMOTE_WS.pk = Config.rsa_pk

# APT_THREAD.start()

loop = asyncio.get_event_loop()
loop.add_signal_handler(signal.SIGTERM, SHUTDOWN_EVENT.set, ())

Config.assets.set_callback(lambda assets, current: WS.broadcast('Scheduler/asset', items=assets, current=current))  # ,

observer = Observer()
# noinspection PyTypeChecker
observer.schedule(UPLOADS, UPLOADS.folder)
observer.start()

UPLOADS.scan_folder()

# if cmdargs.get('remote'):
#     loop.create_task(connect_to_server(cmdargs['remote']), name='remote_control')
# el
if Config.remote_server_ip:
    loop.create_task(api.generators['Settings/Remote/_Remote__connect_to_server'].__original_func__(
        Config.remote_server_ip, Config.remote_server_port), name='remote_control')
elif not cmdargs.no_gui:
    loop.create_task(api.generators['Settings/Remote/_Remote__webview_control_main'].__original_func__(),
                     name='page_controller')


async def info_loop(_ws: WSManager, waiter: asyncio.Event):
    while not waiter.is_set():
        await _ws.broadcast('Settings/info', **get_sysinfo())
        await asyncio.sleep(3)


loop.create_task(info_loop(WS, SHUTDOWN_EVENT), name='info_loop')

loop.create_task(serve(WWW, HyperConfig().from_mapping(  # type: ignore
    bind=f'{cmdargs.https_bind}:{cmdargs.https_port}', insecure_bind=f'{cmdargs.http_bind}:{cmdargs.http_port}',
    certfile=const.certfile, keyfile=const.keyfile, logger_class=Logger
), shutdown_trigger=SHUTDOWN_EVENT.wait), name='server')  # type: ignore

try:
    loop.run_forever()
except KeyboardInterrupt:
    logger.info('Shutdown requested.')
    SHUTDOWN_EVENT.set()
    pass

stop_hostpot()

observer.stop()
# APT_THREAD.join()
