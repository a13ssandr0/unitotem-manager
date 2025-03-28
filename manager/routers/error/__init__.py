from platform import node as get_hostname

from fastapi import Request, HTTPException

from ..templates import TEMPLATES
from .descriptions import descriptions


def http_exception_handler(request: Request, exc: HTTPException):
    return TEMPLATES.TemplateResponse(request, 'errors/error.html.j2', context={
        'hostname': get_hostname(),
        'code': exc.status_code,
        'short_desc': exc.detail,
        'long_desc': descriptions.get(exc.status_code),
    })