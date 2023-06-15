__all__ = [
    "login_redir",
    "LoginForm",
    "LOGMAN",
    "NotAuthenticatedException"
]



from datetime import timedelta
from os import environ, urandom
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, set_key
from fastapi import Form
from fastapi.responses import RedirectResponse
from urllib.parse import quote_plus
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_login import LoginManager
from .configuration import Config


_envfile = Path('/etc/unitotem/unitotem.env')

load_dotenv(_envfile)

if 'auth_token' not in environ:
    environ['auth_token'] = urandom(24).hex()
    _envfile.touch(mode=0o600)
    set_key(_envfile, 'auth_token', environ['auth_token'])


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
                src: Optional[str] = Form(default = None),
                remember_me: Optional[bool] = Form(default = False)):
        super().__init__(grant_type, username, password, scope, client_id, client_secret)
        self.src = src or '/'
        self.remember_me = remember_me



LOGMAN = LoginManager(environ['auth_token'], custom_exception=NotAuthenticatedException,
            token_url='/auth/token', use_cookie=True, use_header=False, default_expiry=timedelta(days=7))

@LOGMAN.user_loader() # type: ignore
async def load_user(username:str):
    return username if username in Config.users else None