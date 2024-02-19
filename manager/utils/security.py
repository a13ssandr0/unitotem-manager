__all__ = [
    "login_redir",
    "login_router",
    "LoginForm",
    "LOGMAN",
    "NotAuthenticatedException"
]

from datetime import timedelta
from os import environ, urandom
from platform import freedesktop_os_release as os_release, node as get_hostname
from typing import Optional
from urllib.parse import quote_plus

from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv, set_key
from fastapi import APIRouter, Form, Depends, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_login import LoginManager
from fastapi_login.exceptions import InvalidCredentialsException
from starlette.requests import Request
from starlette.responses import Response

import utils.constants as const
from .commons import TEMPLATES
from .models import Config
from .network import do_ip_addr

load_dotenv(const.envfile)

if 'auth_token' not in environ:
    environ['auth_token'] = urandom(24).hex()
    const.envfile.touch(mode=0o600)
    set_key(const.envfile, 'auth_token', environ['auth_token'])


class NotAuthenticatedException(Exception):
    pass


async def login_redir(request, exc):
    print(exc)
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


LOGMAN = LoginManager(environ['auth_token'], custom_exception=NotAuthenticatedException,
                      token_url='/auth/token', use_cookie=True, use_header=False, default_expiry=timedelta(days=7))


@LOGMAN.user_loader()  # type: ignore
async def load_user(username: str):
    return username if username in Config.users else None


login_router = APIRouter()


@login_router.post(LOGMAN.tokenUrl)
async def login(data: LoginForm = Depends()):
    if not Config.authenticate(data.username, data.password):
        raise InvalidCredentialsException
    access_token = LOGMAN.create_access_token(data={'sub': data.username})
    resp = RedirectResponse(data.src, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key=LOGMAN.cookie_name, value=access_token,
                    httponly=True, samesite='strict',
                    max_age=int(LOGMAN.default_expiry.total_seconds()) if data.remember_me else None)
    return resp


@login_router.get("/remote/public_key")
async def get_public_key():
    return Response(Config.rsa_pk.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ), media_type="text/plain")


@login_router.get('/login')
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


@login_router.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password: str, username: str = Depends(LOGMAN)):
    Config.change_password(username, password)
    Config.save()
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']
