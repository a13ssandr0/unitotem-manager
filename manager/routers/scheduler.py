from socket import gethostname

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from utils import constants as const
from utils.models.user import User
from routers.login import LOGMAN
from templates import templates
from utils.storage.uploadmanager import upload_manager
from webview_controller.controller import controller

router = APIRouter()


@router.post("/api/scheduler/upload", status_code=status.HTTP_201_CREATED)
async def media_upload(files: list[UploadFile], user: User = Depends(LOGMAN)):
    if not user.has_perm.scheduler:
        raise HTTPException(status_code=403)

    for infile in files:
        await upload_manager.save(infile)


@router.get("/", response_class=HTMLResponse)
async def scheduler(request: Request, user: User = Depends(LOGMAN)):
    template = 'index.html.j2' if user.has_perm.scheduler else 'common/html/base.html.j2'

    return templates.TemplateResponse(request, template, dict(
            logged_user=user,
            disp_size=controller.bounds,
            disk_used=upload_manager.disk_usedh,  # type: ignore
            disk_total=upload_manager.disk_totalh  # type: ignore
    ))
