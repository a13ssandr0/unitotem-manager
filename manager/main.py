import asyncio
import json
import signal
import threading
import time
import warnings
from argparse import ArgumentParser
from functools import cache, lru_cache
from pathlib import Path
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
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from watchdog.observers import Observer
import routers
import utils.constants as const
from api.commons import SHUTDOWN_EVENT
from api.ws.endpoints import REMOTE_WS, WS, api
from api.ws.wsmanager import WSManager
from routers.error import http_exception_handler
from routers.login import NotAuthenticatedException, login_redirect
from templates import templates
from utils._logging import Logger
from utils.models.command_line import cmdargs
from utils.models.playlists import playlists_manager
from utils.models.remote import RemoteManager, remote_manager
from utils.models.user import user_manager
from utils.storage.uploadmanager import upload_manager
from utils.system.network.hotspot import get_hotspot_with_qr, is_hotspot_enabled, start_hotspot, stop_hotspot
from utils.system.network.ip import do_ip_addr
from utils.system.lsblk import start_fs_usage_cache
from utils.system.sysinfo import get_sysinfo
from webview.controller import WebviewManager

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)

# noinspection PyTypeChecker
WWW = FastAPI(
        title='UniTotem', version=const.__version__,
        middleware=[
            Middleware(HTTPSRedirectMiddleware),
        ],
        routes=[
            Mount('/assets', StaticFiles(directory=const.static_folder.joinpath('assets').resolve()), name='assets'),
            Mount('/uploaded', StaticFiles(directory=const.uploads_folder), name='uploaded'),
        ],
        exception_handlers={
            InvalidSignatureError    : login_redirect,
            NotAuthenticatedException: login_redirect,
            HTTPException            : http_exception_handler,
        }
)
WWW.include_router(routers.login.router)
WWW.include_router(routers.websocket.remote.router)
WWW.include_router(routers.websocket.web_ui.router)
WWW.include_router(routers.scheduler.router)
WWW.include_router(routers.backup.router)


@WWW.api_route("/unitotem-{page}", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def first_boot_page(request: Request, page: Union[Literal['first-boot'], Literal['no-assets']]):
    return templates.TemplateResponse(request, f'{page}.html.j2',
                                      {'wifi': await get_hotspot_with_qr() if await is_hotspot_enabled() else None})


parser = ArgumentParser()
parser.add_argument('--no-gui', action='store_true',
                    help='Headless mode: run only the backend, skip the local webview')
parser.add_argument('--http-bind', default=const.default_bind)
parser.add_argument('--http-port', default=const.default_port)
parser.add_argument('--https-bind', default=const.default_bind_secure)
parser.add_argument('--https-port', default=const.default_port_secure)
parser.add_argument('--config', default=const.default_config_file)
parser.add_argument('--version', action='version', version='%(prog)s ' + const.__version__)

loop = asyncio.get_event_loop()
logger.debug('Got event loop {}', id(loop))


# ── graceful shutdown ─────────────────────────────────────────────────────
# Runs inside the asyncio thread; sets SHUTDOWN_EVENT, cleans up, stops loop.

async def _shutdown():
    if not SHUTDOWN_EVENT.is_set():
        SHUTDOWN_EVENT.set()
        await stop_hotspot()
    loop.stop()


def _schedule_shutdown():
    """Thread-safe: schedule _shutdown() in the asyncio event loop."""
    asyncio.run_coroutine_threadsafe(_shutdown(), loop)


# signal.signal works from the main thread regardless of where asyncio runs
signal.signal(signal.SIGTERM, lambda s, f: _schedule_shutdown())
signal.signal(signal.SIGINT,  lambda s, f: _schedule_shutdown())


# ── startup ───────────────────────────────────────────────────────────────

try:
    playlists_manager.load()
except FileNotFoundError:
    logger.warning('First boot or no configuration file found.')
    try:
        if not do_ip_addr(True):
            ssid, passwd = loop.run_until_complete(start_hotspot())
            logger.info(
                    f'Not connected to any network, started fallback hotspot {ssid} with password {passwd}.')
    except Exception as e:
        logger.error(f"Couldn't start wifi hotspot: {e}")

try:
    user_manager.load()
except FileNotFoundError:
    logger.warning('Users configuration file not found. Creating default user "admin" with password "admin".')
    user_manager.add_user('admin', 'admin', {'admin'})

try:
    remote_manager.get_instance()
except FileNotFoundError:
    # the RSA signing key is generated lazily at the first signature,
    # here we only create the default remote connection configuration
    logger.info("Creating default remote connection configuration")
    remote_manager.save()

# APT_THREAD.start()

observer = Observer()
# noinspection PyTypeChecker
observer.schedule(upload_manager, upload_manager.folder)
observer.start()

upload_manager.scan_folder()

# probe unmounted filesystems once and watch mount/umount events
start_fs_usage_cache()

# Initialize WebviewManager and start playlist loops
webview_manager = WebviewManager.init(REMOTE_WS)
for am in playlists_manager.playlists.values():
    webview_manager.add_playlist_loop(am)


# ── async tasks ───────────────────────────────────────────────────────────

if remote_manager.server_ip:
    loop.create_task(api.generators['Settings/Remote/_Remote__connect_to_server'].__original_func__(
            remote_manager.server_ip, remote_manager.server_port), name='remote_control')


async def info_loop(_ws: WSManager, waiter: asyncio.Event):
    while not waiter.is_set():
        # skip collection (incl. disk enumeration) when no admin is watching
        if _ws.active_connections:
            await _ws.broadcast('Settings/info', **get_sysinfo())
        await asyncio.sleep(3)


loop.create_task(info_loop(WS, SHUTDOWN_EVENT), name='info_loop')


async def generate_signing_key():
    # RSA-4096 can take minutes on a Pi: generate it in a worker thread right
    # after startup instead of blocking the loop at the first signature.
    # Progress is observable in the webUI via Settings/Remote/getKeyStatus.
    if RemoteManager.signing_key_status() != 'missing':
        return
    await WS.broadcast('Settings/Remote/getKeyStatus', status='generating')
    try:
        await loop.run_in_executor(None, RemoteManager.get_signing_key)
    except Exception as e:
        logger.error('RSA signing key generation failed: {}', e)
    await WS.broadcast('Settings/Remote/getKeyStatus', status=RemoteManager.signing_key_status())


loop.create_task(generate_signing_key(), name='rsa_keygen')

async def _serve_www():
    # a swallowed bind error would leave the manager up but unreachable:
    # fail loudly and shut down instead
    try:
        await serve(WWW, HyperConfig().from_mapping(
                bind=f'{cmdargs.bind_secure}:{cmdargs.port_secure}', insecure_bind=f'{cmdargs.bind}:{cmdargs.port}',
                certfile=cmdargs.certfile, keyfile=cmdargs.keyfile,
        ), shutdown_trigger=SHUTDOWN_EVENT.wait)
    except PermissionError:
        logger.critical(
                'Cannot bind ports {} and {}: permission denied. Ports below 1024 need root or '
                'CAP_NET_BIND_SERVICE on the python binary (sudo setcap CAP_NET_BIND_SERVICE=+eip '
                '<venv>/bin/python3), or pick higher ports with --port/--port_secure.',
                cmdargs.port, cmdargs.port_secure)
        await _shutdown()
    except Exception as e:
        logger.critical('Web server failed to start: {}', e)
        await _shutdown()


loop.create_task(_serve_www(), name='server')


# ── asyncio thread ────────────────────────────────────────────────────────
# asyncio runs in a background thread so Qt can occupy the main thread
# when running in full (GUI) mode.

asyncio_thread = threading.Thread(target=loop.run_forever, name='asyncio', daemon=True)
asyncio_thread.start()


# ── full mode: Qt webview on main thread ───────────────────────────────────

if not cmdargs.no_gui and not remote_manager.server_ip:
    try:
        from webview.app import WebviewApp

        # No display at all (e.g. a node with no monitor connected, or a
        # systemd service with no access to a graphical session) is a
        # supported, permanent situation, not just a transient one: keep
        # running headless and poll for a display becoming reachable,
        # since constructing WebviewApp with none aborts the whole process
        # natively (SIGABRT, uncatchable) rather than raising an exception.
        if not WebviewApp.display_available():
            logger.warning('No display reachable; running headless until one appears')
            while not SHUTDOWN_EVENT.is_set() and not WebviewApp.display_available():
                time.sleep(5)

        if not SHUTDOWN_EVENT.is_set():
            webview_app = WebviewApp(
                manager_url=f'wss://localhost:{cmdargs.port_secure}/remote',
                instance_id='local-webview',
            )
            # Propagate Qt quit → asyncio shutdown
            webview_app.qt_app.aboutToQuit.connect(_schedule_shutdown)
            logger.info('Starting local Qt6 webview (full mode)')
            webview_app.run()          # blocks until the Qt window is closed
        else:
            asyncio_thread.join()
    except ImportError as e:
        logger.warning('Webview unavailable ({}), running in headless mode', e)
        asyncio_thread.join()
    except Exception as e:
        logger.exception('Qt webview crashed: {}', e)
        _schedule_shutdown()
        asyncio_thread.join(timeout=5)
else:
    # ── headless mode: block main thread until asyncio stops ──────────────
    asyncio_thread.join()


# ── cleanup ───────────────────────────────────────────────────────────────

_schedule_shutdown()
asyncio_thread.join(timeout=5)
observer.stop()
