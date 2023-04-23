from typing import Optional
from fastapi import Form
from fastapi.responses import RedirectResponse
from urllib.parse import quote_plus
from fastapi.security import OAuth2PasswordRequestForm



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
                src: Optional[str] = Form(default = None)):
        super().__init__(grant_type, username, password, scope, client_id, client_secret)
        self.src = src or '/'