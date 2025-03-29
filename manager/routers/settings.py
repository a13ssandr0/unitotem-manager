from traceback import format_exc
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from natsort import natsorted
from psutil import cpu_count, sensors_battery, sensors_fans, sensors_temperatures, virtual_memory
from starlette.requests import Request
from starlette.responses import HTMLResponse

from api import constants as const
from api.commons import UPLOADS
from api.display import DISPLAYS, WINDOW
from utils.models.user import User
from routers.login import LOGMAN
from templates import templates
from utils.system.audio import get_audio_devices
from utils.system.lsblk import lsblk
from utils.system.network.misc import IF_WIRELESS, get_ifaces
from utils.units import human_readable_size

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
@router.get("/settings/{tab}", response_class=HTMLResponse)
async def settings(request: Request, tab: str = 'main_menu', user: User = Depends(LOGMAN)):
    if not (user.has_perm.admin or
            user.has_perm.scheduler and tab in ['playback', 'main_menu'] or
            user.has_perm.audio and tab in ['audio', 'main_menu']):
        raise HTTPException(status_code=403)

    data: dict[str, Any] = dict(
            ut_vers=const.__version__,
            logged_user=user,
            cur_tab=tab,
            disp_size=WINDOW['bounds'],
            disk_used=UPLOADS.disk_usedh,  # type: ignore
            disk_total=UPLOADS.disk_totalh,  # type: ignore
            def_wifi=get_ifaces(IF_WIRELESS)[0]
    )
    match tab:
        case 'audio':
            data['audio'] = get_audio_devices()
        case 'display':
            data['displays'] = DISPLAYS
    try:
        return templates.TemplateResponse(request, f'settings/{tab}.html.j2', data)
    except Exception:
        logger.error(format_exc())


@router.get('/info', response_class=HTMLResponse)
def info(request: Request, user: User = Depends(LOGMAN)):
    return templates.TemplateResponse(request, 'info.html.j2', dict(
            ut_vers=const.__version__,
            logged_user=user,
            disp_size=WINDOW['bounds'],
            disk_used=UPLOADS.disk_usedh,  # type: ignore
            disk_total=UPLOADS.disk_totalh,  # type: ignore
            cpu_count=cpu_count(),
            ram_tot=human_readable_size(virtual_memory().total),
            disks=[blk.model_dump() for blk in lsblk()],
            has_battery=sensors_battery() is not None,
            temp_devs={k: [x._asdict() for x in natsorted(v, key=lambda x: x.label)] for k, v in
                       sensors_temperatures().items()},
            fan_devs={k: [x._asdict() for x in v] for k, v in sensors_fans().items()},
    ))
