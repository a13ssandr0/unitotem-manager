__all__ = [
    "login_redir",
    "login_router",
    "LoginForm",
    "LOGMAN",
    "NotAuthenticatedException"
]

import logging
import time
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
from utils.ws.wsmanager import Context

import utils.constants as const
from .commons import TEMPLATES
from .models import Config, UserData, UserPerms
from .network import do_ip_addr
from .ws.responses import WSBroadcast, WSResponse, WSMulticast
from .ws.wsmanager import WSAPIBase

logger = logging.getLogger(__name__)

load_dotenv(const.envfile)

if 'auth_token' not in environ:
    environ['auth_token'] = urandom(24).hex()
    const.envfile.touch(mode=0o600)
    set_key(const.envfile, 'auth_token', environ['auth_token'])


class NotAuthenticatedException(Exception):
    pass


async def login_redir(request, exc):
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


LOGMAN = LoginManager(environ['auth_token'], not_authenticated_exception=NotAuthenticatedException,
                      token_url='/auth/token', use_cookie=True, use_header=False, default_expiry=timedelta(days=7))


@LOGMAN.user_loader()  # type: ignore
async def load_user(username: str):
    return username if username in Config.users else None


login_router = APIRouter()


@login_router.post(LOGMAN.model.flows.password.tokenUrl)
async def login(data: LoginForm = Depends()):
    if not Config.authenticate(data.username, data.password):
        raise InvalidCredentialsException
    access_token = LOGMAN.create_access_token(data={'sub': data.username, 'cre': time.monotonic()})
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

@login_router.get('/logout')
async def logout():
    resp = RedirectResponse('/')
    resp.set_cookie(key=LOGMAN.cookie_name, value='', httponly=True, samesite='strict', max_age=0)
    return resp


@login_router.post("/api/settings/set_passwd")
async def set_pass(request: Request, response: Response, password: str, username: str = Depends(LOGMAN)):
    Config.change_password(username, password)
    Config.save()
    if 'Referer' in request.headers:
        response.headers['location'] = request.headers['Referer']


class Security(WSAPIBase):
    def getUsers(self):
        return WSBroadcast(self.getUsers, users=[(user, {'perms': list(data.perms)}) for user, data in Config.users.items()])

    def addUser(self, name:str, password:str, perms:set[UserPerms] = None):
        if name in Config.users:
            return WSResponse(self.addUser, error="User already exists")
        Config.add_user(user=name, password=password, perms=perms)
        Config.save()
        return self.getUsers()

    def setUserPass(self, ctx:Context, username:str, password:str):
        if username not in Config.users:
            return WSResponse(self.setUserPass, error=f"User {username} does not exist")
        Config.change_password(username, password)
        Config.save()
        return WSMulticast(ctx.username, 'logout')

    def setUserPerms(self, ctx:Context, username:str, perms:set[UserPerms]):
        if username not in Config.users:
            yield WSResponse(self.setUserPerms, error=f"User {username} does not exist")
            return
        if ctx.username == username and UserPerms.admin in Config.users[username].perms and UserPerms.admin not in perms:
            for user, userdata in Config.users.items():
                if user != ctx.username and UserPerms.admin in userdata.perms:
                    break
            else:
                # we have no other user with user management capabilities cannot continue
                yield WSResponse(self.setUserPerms, error="Cannot remove permissions from the only admin")
                yield self.getUsers()
                return

        Config.users[username].perms = perms
        Config.save()
        yield WSMulticast(ctx.username, 'reload')
        yield self.getUsers()


    def delUser(self, ctx:Context, user:str):
        #Only users with "settings" permissions can manage users and access this method, this means we just have
        #to check that the user is not trying to delete itself and the user is not the only one in the system (maybe redundant)
        if ctx.username == user or len(Config.users) == 1:
            return WSResponse(self.delUser, error="Cannot delete current user")
        elif user in Config.users:
            del Config.users[user]
            Config.save()
        return self.getUsers()

