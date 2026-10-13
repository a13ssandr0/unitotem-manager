"""
What a node answers to another node that is not paired with it yet. Reachable by anybody on the network, by design (a
factory-fresh client has nobody to authenticate against): every endpoint is rate limited per address, refuses bodies of
more than a few KiB, and does nothing that the state of the node does not allow (see utils.remote.pairing for the model).
"""
import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from utils.remote.pairing import MAX_BODY, PairingError
from utils.remote.service import service

router = APIRouter(prefix='/remote')


def _address(request: Request) -> str:
    return request.client.host if request.client else ''


def _limit(limiter, request: Request):
    if not limiter.allow(_address(request)):
        raise HTTPException(status_code=429, detail='Too many requests')


async def _body(request: Request) -> dict:
    raw = b''
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            raise HTTPException(status_code=413, detail='Too big')
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail='Not JSON') from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail='Not an object')
    return data


@router.get('/hello')
async def hello(request: Request):
    """Who this node is: its certificate (public), name, role and whether it can be adopted right now"""
    _limit(service.limit_public, request)
    return service.hello()


@router.post('/adopt')
async def adopt(request: Request):
    """A server adopts this node (an unpaired client only)"""
    _limit(service.limit_adopt, request)
    try:
        return await service.accept_adoption(await _body(request), _address(request))
    except PairingError as e:
        logger.warning('Adoption by {} refused: {}', _address(request), e)
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post('/pair-request')
async def pair_request(request: Request):
    """A client asks this node (a server) to adopt it: the administrator will be asked"""
    _limit(service.limit_request, request)
    try:
        return await service.accept_request(await _body(request), _address(request))
    except PairingError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
