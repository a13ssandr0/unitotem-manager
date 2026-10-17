__all__ = [
    "login_redirect",
    "router",
    "LoginForm",
    "LOGMAN",
    "NotAuthenticatedException",
    "require_perm",
    "session_cookie"
]

import asyncio
import time
from datetime import timedelta
from platform import freedesktop_os_release as os_release, node as gethostname
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_login import LoginManager
from fastapi_login.exceptions import InvalidCredentialsException
from loguru import logger
from starlette.requests import Request
from starlette.responses import Response

import utils.constants as const
from templates import get_vite_assets, is_vite_dev, load_vite_manifest, templates
from utils.environment import environ
from utils.models.user import User, user_manager
from utils.osusers import OSUserError
from utils.system.ip import default_ipv4


class NotAuthenticatedException(Exception):
    pass


async def login_redirect(request, exc):
    logger.error(exc)
    # No administrator can log in yet: the only way forward is the first-access setup
    if not user_manager.has_admin():
        return RedirectResponse('/setup')
    return RedirectResponse('/login?src=' + quote_plus(request.scope.get('path', '/')))


class LoginForm(OAuth2PasswordRequestForm):
    def __init__(self,
                 grant_type: str = Form(default=None, regex="password"),
                 username: str = Form(),
                 password: str = Form(),
                 scope: str = Form(default=""),
                 client_id: Optional[str] = Form(default=None),
                 client_secret: Optional[str] = Form(default=None),
                 src: Optional[str] = Form(default=None),
                 remember_me: Optional[bool] = Form(default=False)):
        super().__init__(
                grant_type=grant_type,
                username=username,
                password=password,
                scope=scope,
                client_id=client_id,
                client_secret=client_secret)
        self.src = src or '/'
        self.remember_me = remember_me


LOGMAN = LoginManager(
        secret=environ.auth_token,
        token_url='/auth/token',
        use_cookie=True,
        use_header=False,
        not_authenticated_exception=NotAuthenticatedException,
        default_expiry=timedelta(days=7)
)
LOGMAN.user_loader()(user_manager.get_user)


def require_perm(perm: str):
    """A route dependency: the session must belong to a user with the given permission (admin has them all)."""

    async def dependency(user: User = Depends(LOGMAN)) -> User:
        if not getattr(user.has_perm, perm):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency

def session_cookie(response: Response, username: str, remember: bool):
    """Puts the session cookie of `username` on a response (a week if `remember`, else until the browser closes)."""
    access_token = LOGMAN.create_access_token(data={'sub': username, 'cre': time.monotonic()})
    response.set_cookie(key=LOGMAN.cookie_name, value=access_token,
                        httponly=True, samesite='strict', secure=True,
                        max_age=int(LOGMAN.default_expiry.total_seconds()) if remember else None)


router = APIRouter()


@router.post(LOGMAN.model.flows.password.tokenUrl)
async def login(data: LoginForm = Depends()):
    # PAM can take a couple of seconds on a wrong password (pam_faildelay): not on the event loop
    if not await asyncio.to_thread(user_manager.authenticate, data.username, data.password):
        logger.error("Invalid credentials for user {}", data.username)
        raise InvalidCredentialsException
    logger.success("User {} logged in", data.username)
    resp = RedirectResponse(data.src, status_code=status.HTTP_303_SEE_OTHER)
    session_cookie(resp, data.username, data.remember_me)
    return resp


@router.get("/login/users")
async def get_users() -> list[str]:
    return list(user_manager.users())

@router.get('/login')
async def login_page(request: Request, src: Optional[str] = '/'):
    if not user_manager.has_admin():
        return RedirectResponse('/setup')
    try:
        await LOGMAN(request)
        # why are you trying to access login page from an authenticated session?
        return RedirectResponse(src or '/')
    except NotAuthenticatedException:
        pass

    ## USE ONLY IN DEBUG
    load_vite_manifest.cache_clear()
    get_vite_assets.cache_clear()
    ##
    assets = get_vite_assets("src/pages/login.vue", is_vite_dev(request))
    return templates.TemplateResponse(request, "base.html", {
        "request": request,
        "assets": assets,
        "state": {
            "src": src or '/',
            "ut_vers": const.__version__,
            "os_vers": os_release()['PRETTY_NAME'],
            "ip_addr": default_ipv4(),
            "hostname": gethostname(),
        },
    })


@router.get('/logout')
async def logout():
    resp = RedirectResponse('/')
    resp.set_cookie(key=LOGMAN.cookie_name, value='', httponly=True, samesite='strict', max_age=0)
    return resp


@router.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password: str, user: User = Depends(LOGMAN)):
    from utils import passwords
    from utils.models.security import security_settings
    if security_settings.enforce_password_policy and (problems := passwords.validate_password(password, user.name)):
        raise HTTPException(status_code=422, detail=problems)
    try:
        user_manager.change_password(user.name, password)
    except OSUserError as e:
        raise HTTPException(status_code=422, detail=[str(e)])
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']
