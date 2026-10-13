import asyncio
import json
import signal
import sys
import threading
import warnings
from argparse import ArgumentParser
from functools import cache, lru_cache
from pathlib import Path
from typing import Literal, Union

import urllib3
from fastapi import (Depends, FastAPI, HTTPException, Request)
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
import utils.plugins as plugins
import utils.ready as ready
from api.commons import SHUTDOWN_EVENT
from api.ws.endpoints import REMOTE_WS, WS, api
from api.ws.wsmanager import WSManager
from routers.error import http_exception_handler
from routers.login import LOGMAN, NotAuthenticatedException, login_redirect
from templates import get_vite_assets, public_asset_files, templates
from utils._logging import Logger
from utils.models.command_line import cmdargs
from utils.models.playlists import playlists_manager
from utils.models.remote import RemoteManager, remote_manager
from utils.remote.service import service as remote_service
from utils.models.user import user_manager
from utils.storage.uploadmanager import upload_manager
from utils.system.lsblk import start_fs_usage_cache
from utils.system.sysinfo import get_sysinfo
from webview.controller import WebviewManager

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)

class AssetFiles(StaticFiles):
    """
    The built web UI. CSS, fonts and images are public, and so is the JavaScript the login page
    needs; the chunks that make up the pages of the app are only served to authenticated sessions,
    so an anonymous visitor cannot even download the interface (the data is protected separately,
    by the websocket).
    """

    async def get_response(self, path: str, scope):
        client = scope.get('client')
        local = client is not None and client[0] in ('127.0.0.1', '::1')
        # The local viewer has no session: it loads the PDF page (and its scripts) from this same machine
        if not local and path.endswith('.js') and f'assets/{path}' not in public_asset_files():
            try:
                await LOGMAN(Request(scope))
            except NotAuthenticatedException:
                raise HTTPException(status_code=401)
        return await super().get_response(path, scope)


class UploadedFiles(StaticFiles):
    """
    The uploaded media. The local viewer fetches them from this same machine, and people logged in to the web
    UI download them; nobody else on the network gets to read what was uploaded.
    """

    async def get_response(self, path: str, scope):
        client = scope.get('client')
        if client is None or client[0] not in ('127.0.0.1', '::1'):
            try:
                await LOGMAN(Request(scope))
            except NotAuthenticatedException:
                raise HTTPException(status_code=401)
        return await super().get_response(path, scope)


# noinspection PyTypeChecker
WWW = FastAPI(
        title='UniTotem', version=const.__version__,
        middleware=[
            Middleware(HTTPSRedirectMiddleware),
        ],
        routes=[
            Mount('/assets', AssetFiles(directory=const.static_folder.joinpath('assets').resolve()), name='assets'),
            Mount('/uploaded', UploadedFiles(directory=const.uploads_folder), name='uploaded'),
        ],
        exception_handlers={
            InvalidSignatureError    : login_redirect,
            NotAuthenticatedException: login_redirect,
            HTTPException            : http_exception_handler,
        }
)
# Registered BEFORE the routers below, because routers.scheduler ends in a
# catch-all "/{path:path}" that requires a session: with these two declared
# after it, the catch-all matched first and the device's own screen showed the
# login form instead of the welcome screen - on a first boot, where the
# hotspot SSID, password and QR code are the only way in, and where nobody
# can log in anyway since the screen is bolted to a wall.
@WWW.api_route("/unitotem-{page}", response_class=HTMLResponse, methods=['GET', 'HEAD'])
async def first_boot_page(request: Request, page: Union[Literal['first-boot'], Literal['no-assets']]):
    """
    The two pages the local viewer shows on its own: the first-boot welcome
    screen and the "no assets" placeholder.

    Served without authentication, and only to loopback. They are only ever
    fetched by the viewer running on this same machine, so anyone reaching
    them from the network is not a legitimate caller - and the first-boot page
    carries the hotspot's SSID and password, which must not be readable by
    whoever happens to be on that network. Non-local callers get a 404 rather
    than a 403, which does not confirm that the page exists at all.
    """
    if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
        logger.warning('Rejected non-local request for /unitotem-{} from {}',
                       page, request.client.host if request.client else 'unknown')
        raise HTTPException(status_code=404)
    context = {'wifi': None}
    for _plugin, hook in plugins.each('first_boot_context'):
        context.update(await hook())
    return templates.TemplateResponse(request, f'{page}.html.j2', context)


@WWW.api_route("/health", methods=['GET', 'HEAD'])
async def health(request: Request):
    """
    Readiness probe for the local update machinery (boot confirmation). Loopback only, no
    session: it answers 200 once the server is accepting requests.
    """
    if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(status_code=404)
    return {'status': 'ok', 'version': const.__version__}


@WWW.get('/api/schema', dependencies=[Depends(LOGMAN)])
async def api_schema():
    """Every websocket API function with its permissions and argument schema, for the web UI client."""
    return api.schema


@WWW.get('/pdf-view', response_class=HTMLResponse)
async def pdf_view(request: Request):
    """The page the local viewer shows for one page of a PDF. Loopback only, like the other pages made for the viewer."""
    if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(status_code=404)
    js = get_vite_assets('src/pdf-view.js')['js']
    return HTMLResponse('<!doctype html><html><head><meta charset="utf-8"><title>PDF</title></head><body>'
                        f'<script type="module" src="{js}"></script></body></html>')


WWW.include_router(routers.login.router)
WWW.include_router(routers.websocket.remote.router)
WWW.include_router(routers.websocket.web_ui.router)
# Before the scheduler router, which ends in a catch-all that requires a session
for _plugin, _factory in plugins.each('routers'):
    for _router in _factory():
        WWW.include_router(_router)
WWW.include_router(routers.backup.router)
WWW.include_router(routers.scheduler.router)


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
        try:
            await asyncio.wait_for(remote_service.stop(), 5)
        except Exception:
            logger.exception('The link with the other nodes did not shut down cleanly')
        for _plugin, hook in plugins.each('on_shutdown'):
            try:
                await hook()
            except Exception:
                logger.exception('Plugin {} failed to shut down cleanly', _plugin.name)
    loop.stop()


def _schedule_shutdown(from_gui=False):
    """Thread-safe: schedule _shutdown() in the asyncio event loop. `from_gui`: the windows are the ones that ended."""
    asyncio.run_coroutine_threadsafe(_shutdown(), loop)
    # With quitOnLastWindowClosed disabled, closing the last window does not make Qt's exec() return on its own, so a
    # real shutdown must ask the windows to quit, or the main thread (and so the whole process) never exits.
    if not from_gui:
        for _plugin, hook in plugins.each('request_quit'):
            hook()


# signal.signal works from the main thread regardless of where asyncio runs
signal.signal(signal.SIGTERM, lambda s, f: _schedule_shutdown())
signal.signal(signal.SIGINT,  lambda s, f: _schedule_shutdown())


# ── startup ───────────────────────────────────────────────────────────────

try:
    playlists_manager.load()
except FileNotFoundError:
    logger.warning('First boot or no configuration file found.')
    for _plugin, hook in plugins.each('on_first_boot'):
        loop.run_until_complete(hook())

try:
    user_manager.load()
except FileNotFoundError:
    logger.warning('Users configuration file not found. Creating default user "admin" with password "admin".')
    user_manager.add_user('admin', 'admin', {'admin'})

if not cmdargs.remote_file.exists():
    # the RSA signing key is generated lazily at the first signature,
    # here we only create the default remote connection configuration (and the identity of the node)
    logger.info("Creating default remote connection configuration")
    remote_manager.ensure_identity()

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
# After the loops exist, so assignments pointing at a playlist that is gone can
# be recognised and dropped. Viewers are not connected yet: each one picks its
# playlist back up when it registers.
webview_manager.load_assignments()


# ── async tasks ───────────────────────────────────────────────────────────

# The listener for paired clients (server role) or the connection to the server (client role)
loop.create_task(remote_service.start(), name='remote_service')


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
    # Progress is observable in the webUI via Settings/Remote/key_status.
    if RemoteManager.signing_key_status() != 'missing':
        return
    await WS.broadcast('Settings/Remote/key_status', status='generating')
    try:
        await loop.run_in_executor(None, RemoteManager.get_signing_key)
    except Exception as e:
        logger.error('RSA signing key generation failed: {}', e)
    await WS.broadcast('Settings/Remote/key_status', status=RemoteManager.signing_key_status())


loop.create_task(generate_signing_key(), name='rsa_keygen')


for _plugin, hook in plugins.each('on_startup'):
    hook(loop)


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

# With a local viewer, READY is its first frame on screen (it ends the boot splash); otherwise it is the server answering
_gui = next((hook for _plugin, hook in plugins.each('run_gui')), None)
_viewer_will_notify = bool(_gui and not cmdargs.no_gui and not remote_manager.is_paired_client)


async def _ready_when_serving():
    for _ in range(600):
        try:
            _, writer = await asyncio.open_connection('127.0.0.1', cmdargs.port_secure)
            writer.close()
            break
        except OSError:
            await asyncio.sleep(0.1)
    if not _viewer_will_notify:
        ready.ready('web server answering')


loop.create_task(_ready_when_serving(), name='ready')


# ── asyncio thread ────────────────────────────────────────────────────────
# asyncio runs in a background thread so Qt can occupy the main thread
# when running in full (GUI) mode.

asyncio_thread = threading.Thread(target=loop.run_forever, name='asyncio', daemon=True)
asyncio_thread.start()


# ── the local windows, on the main thread, if the viewer is installed ─────

exit_status = 0
if _viewer_will_notify:
    try:
        if not _gui(_schedule_shutdown):
            asyncio_thread.join()           # the windows cannot run here: serve without them
    except Exception as e:
        logger.exception('The viewer stopped: {}', e)
        exit_status = 1                     # so that systemd restarts the service
        ready.ready('viewer failed')
        _schedule_shutdown()
        asyncio_thread.join(timeout=5)
else:
    # ── no local windows: block the main thread until asyncio stops ───────
    asyncio_thread.join()


# ── cleanup ───────────────────────────────────────────────────────────────

_schedule_shutdown()
asyncio_thread.join(timeout=5)
observer.stop()
sys.exit(exit_status)
