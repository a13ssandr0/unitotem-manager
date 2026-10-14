"""The listener of a server node: who gets in, who is refused and how, with real TLS on loopback."""
import asyncio
import ssl
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import websockets
from websockets.exceptions import InvalidStatus

import utils.constants as _constants
_constants.uploads_folder = Path(tempfile.mkdtemp(prefix='unitotem-uploads-'))

from api.ws.endpoints import REMOTE_WS  # noqa: E402
from utils.models import remote as remote_module  # noqa: E402
from utils.models.remote import RemoteManager  # noqa: E402
from utils.remote import identity as ident  # noqa: E402
from utils.remote import link as link_module  # noqa: E402
from utils.remote.link import LinkServer, decode_frame, control_frame, is_control, parse_control  # noqa: E402
from utils.remote.tls import client_context  # noqa: E402


@pytest.fixture
def server(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setattr(remote_module, 'cmdargs', SimpleNamespace(remote_file=tmp_path / 'remote.json'))
    monkeypatch.setattr(link_module, 'cmdargs', SimpleNamespace(bind_link='127.0.0.1', port_link=0))
    monkeypatch.setattr(RemoteManager, '_instance', None)
    rm = RemoteManager.get_instance()
    rm.ensure_identity()
    yield rm
    RemoteManager._instance = None


async def _connect(rm, port, identity, **kwargs):
    ctx = client_context(*identity, rm.identity_cert)
    return await websockets.connect(f'wss://127.0.0.1:{port}/remote', ssl=ctx, open_timeout=5,
                                    additional_headers={'hostname': 'lobby', 'port': '443'}, **kwargs)


def _scenario(rm, body):
    async def main():
        changes = []
        srv = LinkServer(REMOTE_WS, lambda: changes.append(1))
        await srv.start()
        port = srv._server.sockets[0].getsockname()[1]
        try:
            return await body(srv, port, changes)
        finally:
            await srv.stop()

    return asyncio.run(main())


def test_a_paired_client_connects_is_known_and_recorded(server):
    identity = ident.generate_identity('client')
    peer = server.pin_client(identity[0], 'old-name', None)

    async def body(srv, port, changes):
        ws = await _connect(server, port, identity)
        await asyncio.sleep(0.2)
        assert srv.connected() == {peer.id}
        assert server.peers[peer.id].name == 'lobby'            # the name it told, the address it came from
        assert server.peers[peer.id].ip == '127.0.0.1'
        await ws.close()
        await asyncio.sleep(0.2)
        assert srv.connected() == set()
        assert changes                                          # the page is told, both times

    _scenario(server, body)


def test_unknown_certificate_never_gets_a_websocket(server):
    paired = ident.generate_identity('client')
    server.pin_client(paired[0], 'lobby', None)
    stranger = ident.generate_identity('stranger')

    async def body(srv, port, changes):
        with pytest.raises((ssl.SSLError, ConnectionError, websockets.exceptions.WebSocketException, OSError)):
            ws = await _connect(server, port, stranger)
            await ws.send('hello')
            await asyncio.wait_for(ws.recv(), 3)
        assert srv.connected() == set()

    _scenario(server, body)


def test_disabled_client_is_refused_with_403_and_dropped_when_disabled(server):
    identity = ident.generate_identity('client')
    peer = server.pin_client(identity[0], 'lobby', None)

    async def body(srv, port, changes):
        ws = await _connect(server, port, identity)
        await asyncio.sleep(0.2)
        assert srv.connected() == {peer.id}
        peer.enabled = False
        await srv.disconnect(peer.id)
        await asyncio.sleep(0.2)
        assert srv.connected() == set()
        with pytest.raises(InvalidStatus) as refused:
            await _connect(server, port, identity)
        assert refused.value.response.status_code == 403
        peer.enabled = True
        ws = await _connect(server, port, identity)             # temporary: enabled again, it is back
        await asyncio.sleep(0.2)
        assert srv.connected() == {peer.id}
        await ws.close()

    _scenario(server, body)


def test_a_client_unpaired_while_the_listener_runs_is_refused_even_though_its_certificate_is_still_loaded(server):
    """A certificate cannot be removed from a live TLS context: the application check must stop it anyway"""
    identity = ident.generate_identity('client')
    peer = server.pin_client(identity[0], 'lobby', None)

    async def body(srv, port, changes):
        server.forget_client(peer.id)
        with pytest.raises(InvalidStatus) as refused:           # TLS accepts it, HTTP does not
            await _connect(server, port, identity)
        assert refused.value.response.status_code == 403
        await srv.reload()                                      # and a reload takes it out of the context for good
        new_port = srv._server.sockets[0].getsockname()[1]
        with pytest.raises(Exception):
            await _connect(server, new_port, identity)

    _scenario(server, body)


def test_a_second_connection_of_the_same_client_replaces_the_first(server):
    identity = ident.generate_identity('client')
    peer = server.pin_client(identity[0], 'lobby', None)

    async def body(srv, port, changes):
        first = await _connect(server, port, identity)
        await asyncio.sleep(0.2)
        second = await _connect(server, port, identity)
        await asyncio.sleep(0.3)
        assert srv.connected() == {peer.id}
        with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
            await asyncio.wait_for(first.recv(), 2)
        assert closed.value.rcvd.code == link_module.CLOSE_REPLACED
        await second.close()

    _scenario(server, body)


def test_the_listener_is_not_started_for_a_client_node(server):
    server.role = 'client'

    async def main():
        srv = LinkServer(REMOTE_WS, lambda: None)
        await srv.start()
        return srv.running

    assert asyncio.run(main()) is False


def test_frames():
    import json
    from base64 import b64encode
    payload = {'target': 'Show', 'src': 'x'}
    signed = b64encode(json.dumps(payload).encode()).decode() + '.' + b64encode(b'sig').decode()
    assert decode_frame(signed) == payload and decode_frame(json.dumps(payload)) == payload
    frame = control_frame(link='hello', n=1)
    assert is_control(frame) and is_control(b'\x00') and not is_control(signed) and not is_control(json.dumps(payload))
    assert parse_control(frame) == {'link': 'hello', 'n': 1}
