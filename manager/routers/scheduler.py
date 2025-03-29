from platform import node as get_hostname

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from api import constants as const
from api.commons import UPLOADS
from api.display import WINDOW
from utils.models.user import User
from routers.login import LOGMAN
from templates import templates

router = APIRouter()


@router.post("/api/scheduler/upload", status_code=status.HTTP_201_CREATED)
async def media_upload(files: list[UploadFile], user: User = Depends(LOGMAN)):
    if not user.has_perm.scheduler:
        raise HTTPException(status_code=403)

    for infile in files:
        await UPLOADS.save(infile)


@router.get("/", response_class=HTMLResponse)
async def scheduler(request: Request, user: User = Depends(LOGMAN)):
    template = 'index.html.j2' if user.has_perm.scheduler else 'common/html/base.html.j2'

    return templates.TemplateResponse(request, template, dict(
            ut_vers=const.__version__,
            logged_user=user,
            hostname=get_hostname(),
            disp_size=WINDOW['bounds'],
            disk_used=UPLOADS.disk_usedh,  # type: ignore
            disk_total=UPLOADS.disk_totalh  # type: ignore
    ))
