from platform import freedesktop_os_release as os_release, node as gethostname

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

import utils.constants as const
from routers.login import LOGMAN
from templates import get_vite_assets, load_vite_manifest, templates
from utils.models.user import User
from utils.storage.uploadmanager import upload_manager
from utils.system.network.ip import do_ip_addr

router = APIRouter()


@router.post("/api/scheduler/upload", status_code=status.HTTP_201_CREATED)
async def media_upload(files: list[UploadFile], user: User = Depends(LOGMAN)):
    if not user.has_perm.scheduler:
        raise HTTPException(status_code=403)

    for infile in files:
        await upload_manager.save(infile)


# @router.get("/", response_class=HTMLResponse)
# async def scheduler(request: Request, user: User = Depends(LOGMAN)):
#     template = 'index.html.j2' if user.has_perm.scheduler else 'common/html/base.html.j2'
#
#     return templates.TemplateResponse(request, template, dict(
#             logged_user=user,
#             disp_size=Controller.get_instance().bounds,
#             disk_used=upload_manager.disk_usedh,  # type: ignore
#             disk_total=upload_manager.disk_totalh  # type: ignore
#     ))




@router.get("/{path:path}", response_class=HTMLResponse)
async def home(request: Request, path: str, user: User = Depends(LOGMAN)):
    ## USE ONLY IN DEBUG
    load_vite_manifest.cache_clear()
    get_vite_assets.cache_clear()
    ##
    assets = get_vite_assets("src/main.js")
    return templates.TemplateResponse(request, "base.html", {
        "assets": assets,
        "state": {
            "user": user.name,
            "ut_vers": const.__version__,
            "os_vers": os_release()['PRETTY_NAME'],
            "ip_addr": ip['addr'][0]['addr'] if (ip:=do_ip_addr(True)) else None,
            "hostname": gethostname(),
        },
    })
