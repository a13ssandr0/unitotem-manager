__all__ = [
    "login_redirect",
    "router",
    "LoginForm",
    "LOGMAN",
    "NotAuthenticatedException"
]

import time
from datetime import timedelta
from typing import Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_login import LoginManager
from fastapi_login.exceptions import InvalidCredentialsException
from loguru import logger
from starlette.requests import Request
from starlette.responses import Response

from templates import templates
from utils.environment import environ
from utils.models.remote import remote_manager
from utils.models.user import User, user_manager


class NotAuthenticatedException(Exception):
    pass


async def login_redirect(request, exc):
    logger.error(exc)
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

router = APIRouter()


@router.post(LOGMAN.model.flows.password.tokenUrl)
async def login(data: LoginForm = Depends()):
    if not user_manager.authenticate(data.username, data.password):
        raise InvalidCredentialsException
    access_token = LOGMAN.create_access_token(data={'sub': data.username, 'cre': time.monotonic()})
    resp = RedirectResponse(data.src, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(key=LOGMAN.cookie_name, value=access_token,
                    httponly=True, samesite='strict',
                    max_age=int(LOGMAN.default_expiry.total_seconds()) if data.remember_me else None)
    return resp


@router.get("/remote/public_key")
async def get_public_key():
    return Response(remote_manager.rsa_prik.public_key().exportKey(), media_type="text/plain")


@router.get('/login')
async def login_page(request: Request, src: Optional[str] = '/'):
    try:
        await LOGMAN(request)
        # why are you trying to access login page from an authenticated session?
        return RedirectResponse(src or '/')
    except NotAuthenticatedException:
        pass

    return templates.TemplateResponse(request, 'login.html.j2', {'src': src})


@router.get('/logout')
async def logout():
    resp = RedirectResponse('/')
    resp.set_cookie(key=LOGMAN.cookie_name, value='', httponly=True, samesite='strict', max_age=0)
    return resp


@router.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password: str, user: User = Depends(LOGMAN)):
    user_manager.change_password(user.name, password)
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']
