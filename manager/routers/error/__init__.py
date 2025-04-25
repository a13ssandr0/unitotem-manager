from fastapi import HTTPException, Request

from routers.error.descriptions import descriptions
from templates import templates


def http_exception_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(request, 'errors/error.html.j2', context={
        'code'      : exc.status_code,
        'short_desc': exc.detail,
        'long_desc' : descriptions.get(exc.status_code),
    })
