"""
First access: the setup page that turns an unclaimed node into one with an administrator (see utils/claim.py).

`/setup` exists only while the node is unclaimed and is a 404 forever after. Everything else the node serves is
unreachable meanwhile: the login redirect points here (routers/login.py) and the API has nobody to authenticate.
"""
import asyncio
import json
from platform import freedesktop_os_release as os_release, node as gethostname
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

import utils.constants as const
import utils.plugins as plugins
from routers.login import session_cookie
from templates import get_vite_assets, is_vite_dev, load_vite_manifest, templates
from utils import passwords
from utils.claim import ClaimError, Locked, claim_manager
from utils.system.ip import default_ipv4

router = APIRouter()


class SetupRequest(BaseModel):
    """Everything is bounded: whoever can reach this page can send anything to it."""
    code: str = Field(max_length=64)
    username: str = Field(max_length=64)
    password: str = Field(max_length=passwords.MAX_LENGTH * 4)
    os_password: Literal['same', 'different', 'skip'] = 'skip'
    os_password_value: Optional[str] = Field(default=None, max_length=passwords.MAX_LENGTH * 4)


def _loopback(request: Request) -> bool:
    return request.client is not None and request.client.host in ('127.0.0.1', '::1')


def _os_account() -> Optional[str]:
    return next((hook() for _plugin, hook in plugins.each('os_account')), None)


async def _set_os_password(password: str) -> bool:
    for _plugin, hook in plugins.each('set_os_password'):
        return await hook(password)
    return False


@router.get('/setup')
async def setup_page(request: Request):
    if not claim_manager.unclaimed:
        raise HTTPException(status_code=404)
    load_vite_manifest.cache_clear()
    get_vite_assets.cache_clear()
    return templates.TemplateResponse(request, 'base.html', {
        'assets': get_vite_assets('src/pages/setup.vue', is_vite_dev(request)),
        'state': {
            'ut_vers': const.__version__,
            'os_vers': os_release()['PRETTY_NAME'],
            'ip_addr': default_ipv4(),
            'hostname': gethostname(),
            'rules': passwords.RULES,
            'min_length': passwords.MIN_LENGTH,
            'max_length': passwords.MAX_LENGTH,
            'os_account': _os_account(),
        },
    })


@router.post('/setup')
async def setup(data: SetupRequest):
    if not claim_manager.unclaimed:
        raise HTTPException(status_code=404)
    os_account = _os_account()
    # The terminal account's password is checked before anything is created, so a refusal costs nothing
    os_secret = None
    if os_account is not None and data.os_password != 'skip':
        os_secret = data.password if data.os_password == 'same' else data.os_password_value
        problems = passwords.validate_password(os_secret or '', os_account)
        if problems:
            return JSONResponse({'detail': problems}, status_code=422)
    try:
        claim_manager.claim(data.code, data.username, data.password)
    except Locked as exc:
        return JSONResponse({'detail': [str(exc)], 'retry_after': exc.retry_after}, status_code=429,
                            headers={'Retry-After': str(exc.retry_after)})
    except ClaimError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404)
        return JSONResponse({'detail': getattr(exc, 'problems', [str(exc)])}, status_code=exc.status)

    if os_account is None:
        os_result = 'unavailable'
    elif os_secret is None:
        os_result = 'skipped'
    else:
        os_result = 'set' if await _set_os_password(os_secret) else 'failed'
    logger.info('First access completed from the setup page (operating-system account: {})', os_result)
    response = JSONResponse({'ok': True, 'os_password': os_result, 'os_account': os_account})
    session_cookie(response, data.username, remember=True)
    return response


@router.get('/unitotem-claim/events')
async def claim_events(request: Request):
    """
    What the totem screen shows about the claim, as server-sent events: the state when the page connects and again
    every time it changes (a new code after too many wrong ones, or the node being claimed). Loopback only: the code
    is for whoever stands at the device, never for the network.
    """
    if not _loopback(request):
        raise HTTPException(status_code=404)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    unsubscribe = claim_manager.subscribe(lambda state: loop.call_soon_threadsafe(queue.put_nowait, state))

    async def stream():
        try:
            yield f'data: {json.dumps(claim_manager.state())}\n\n'
            while True:
                try:
                    yield f'data: {json.dumps(await asyncio.wait_for(queue.get(), 25))}\n\n'
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'        # lets a dead connection be noticed
        finally:
            unsubscribe()

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})
