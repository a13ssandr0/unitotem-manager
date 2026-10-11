import base64
from pathlib import Path

from fastapi import HTTPException, Request

from routers.error.descriptions import descriptions
from templates import templates

# Error pages are served to anyone, logged in or not, so they must not say anything about the system
# (no version, OS, address or hostname).
_IMAGES = {}
_image_file = Path(__file__).parents[2].joinpath('resources/404.png')
if _image_file.exists():
    _IMAGES[404] = 'data:image/png;base64,' + base64.b64encode(_image_file.read_bytes()).decode()


def http_exception_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(request, 'errors/error.html.j2', status_code=exc.status_code, context={
        'code'      : exc.status_code,
        'short_desc': exc.detail,
        'long_desc' : descriptions.get(exc.status_code),
        'image'     : _IMAGES.get(exc.status_code),
    })
