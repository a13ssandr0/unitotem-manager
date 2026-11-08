"""The errors of the web UI websocket name the command they answer (its target), so only the page that sent it reacts."""
import asyncio
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from api.ws.permissions import APIPermissionError
from routers.websocket import web_ui


class FakeSocket:
    def __init__(self, frames):
        self.cookies = {}
        self.frames = list(frames)

    async def receive_json(self):
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame


@pytest.fixture
def sent(monkeypatch):
    replies = []

    async def send(websocket, target, /, nocache=False, **kwargs):
        replies.append((target, kwargs))

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(web_ui, 'LOGMAN', noop)
    monkeypatch.setattr(web_ui, 'WS', SimpleNamespace(connect=noop, send=send, disconnect=lambda *a: None))
    monkeypatch.setattr(web_ui.sessions, 'track', lambda *a: None)
    monkeypatch.setattr(web_ui.sessions, 'untrack', lambda *a: None)
    return replies


def run_frames(monkeypatch, frames, sent):
    user = SimpleNamespace(name='admin', sid='s')

    async def login(request):
        return user

    async def handle_call(target, user, request_data):
        if target == 'Unknown/command':
            raise KeyError(target)
        if target == 'Denied/command':
            raise APIPermissionError()
        if target == 'Broken/command':
            raise RuntimeError('boom')
        yield web_ui.WSResponse(error='reply already named')
    monkeypatch.setattr(web_ui, 'LOGMAN', login)
    monkeypatch.setattr(web_ui, 'handle_call', handle_call)
    asyncio.run(web_ui.websocket_endpoint(FakeSocket(frames)))
    return [reply for reply in sent if reply[0] != 'connected']     # the greeting is not an answer to a command


def test_each_error_envelope_carries_the_command_it_answers(monkeypatch, sent):
    frames = [{'target': 'Unknown/command', 'x': 1}, {'target': 'Denied/command'}, {'target': 'Broken/command'},
              {'no': 'target'}, WebSocketDisconnect()]
    replies = run_frames(monkeypatch, frames, sent)
    assert [t for t, _ in replies] == ['Unknown/command', 'Denied/command', 'Broken/command', 'error']
    assert replies[0][1]['error'] == 'Invalid command' and 'Unknown/command' in replies[0][1]['extra']
    assert replies[1][1]['error'] == 'Permission error: not allowed to execute Denied/command'
    assert replies[2][1] == {'error': 'Internal error'}
    assert replies[3][1] == {'error': 'Internal error'}


def test_a_failed_frame_does_not_inherit_the_target_of_the_previous_one(monkeypatch, sent):
    frames = [{'target': 'Denied/command'}, ValueError('not JSON'), WebSocketDisconnect()]
    replies = run_frames(monkeypatch, frames, sent)
    assert [t for t, _ in replies] == ['Denied/command', 'error']
