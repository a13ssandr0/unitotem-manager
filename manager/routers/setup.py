"""
First access: the setup page that gives a node without an administrator its first one.

`/setup` exists only while no administrator can log in, and is a 404 from then on. The account is a system account like
every other one (utils/osusers.py): the user name and password chosen here are the ones PAM checks. Everything else the
node serves is unreachable meanwhile: the login redirect points here (routers/login.py).
"""
import asyncio
from platform import freedesktop_os_release as os_release, node as gethostname

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

import utils.constants as const
from routers.login import LOGMAN, session_cookie
from templates import get_vite_assets, is_vite_dev, load_vite_manifest, templates
from utils import osusers, passwords
from utils.models.user import UserData, UserPerms, user_manager
from utils.system.ip import reachable_ipv4
from webview.controller import WebviewManager

router = APIRouter()
_lock = asyncio.Lock()        # two simultaneous setups: one wins, the other finds the node already set up


class SetupRequest(BaseModel):
    """Everything is bounded: whoever can reach this page can send anything to it."""
    username: str = Field(max_length=64)
    password: str = Field(max_length=passwords.MAX_LENGTH * 4)


@router.get('/setup')
async def setup_page(request: Request):
    if user_manager.has_admin():
        raise HTTPException(status_code=404)
    load_vite_manifest.cache_clear()
    get_vite_assets.cache_clear()
    return templates.TemplateResponse(request, 'base.html', {
        'assets': get_vite_assets('src/pages/setup.vue', is_vite_dev(request)),
        'state': {
            'ut_vers': const.__version__,
            'os_vers': os_release()['PRETTY_NAME'],
            'ip_addr': reachable_ipv4(),
            'hostname': gethostname(),
            'rules': passwords.RULES,
            'min_length': passwords.MIN_LENGTH,
            'max_length': passwords.MAX_LENGTH,
        },
    })


@router.post('/setup')
async def setup(data: SetupRequest):
    async with _lock:
        if user_manager.has_admin():
            raise HTTPException(status_code=404)
        problems = passwords.validate_password(data.password, data.username)
        if problems:
            return JSONResponse({'detail': problems}, status_code=422)
        try:
            # useradd and chpasswd take a moment: not on the event loop
            await asyncio.to_thread(osusers.create_user, data.username, data.password)
        except osusers.OSUserError as e:
            return JSONResponse({'detail': [str(e)]}, status_code=409)
        user_manager[data.username] = UserData(permissions={UserPerms.admin})
    logger.info('First access completed: administrator {} created', data.username)
    if WebviewManager._instance is not None:
        WebviewManager._instance.refresh_idle()     # the welcome screen of the totem gives way to the placeholder
    response = JSONResponse({'ok': True})
    session_cookie(response, data.username, remember=True)
    return response
