"""Identity, pinning and the rejection of a certificate nobody paired: a real TLS listener and real clients on loopback."""
import asyncio
import ssl

import pytest
import websockets
from websockets.asyncio.server import serve

from utils.remote.identity import (check_pair, cert_der, fingerprint, format_fingerprint, generate_identity, node_id)
from utils.remote.tls import client_context, peer_fingerprint, server_context


@pytest.fixture(scope='module')
def nodes():
    """server, client (paired with it) and two strangers: a rogue server and a rogue client"""
    return {name: generate_identity(name) for name in ('server', 'client', 'rogue-server', 'rogue-client')}


def test_identity_is_stable_and_consistent(nodes):
    cert, key = nodes['server']
    assert check_pair(cert, key)
    assert not check_pair(cert, nodes['client'][1])
    assert fingerprint(cert) == fingerprint(cert_der(cert))
    assert len(fingerprint(cert)) == 64 and node_id(cert) == fingerprint(cert)[:32]
    assert format_fingerprint('abcd12') == 'AB:CD:12'
    assert fingerprint(cert) != fingerprint(nodes['client'][0])


def test_not_a_certificate():
    with pytest.raises(ValueError):
        fingerprint('definitely not a certificate')
    with pytest.raises(ValueError):
        fingerprint(b'\x00\x01\x02')


async def _echo(ws):
    fp = peer_fingerprint(ws.transport.get_extra_info('ssl_object'))
    async for msg in ws:
        await ws.send(f'{fp}:{msg}')


def _run(server_ctx, client_ctx):
    """Start a listener, connect, say hello. Returns the answer, or the exception the connection died of."""

    async def main():
        async with serve(_echo, '127.0.0.1', 0, ssl=server_ctx) as server:
            port = server.sockets[0].getsockname()[1]
            try:
                async with websockets.connect(f'wss://127.0.0.1:{port}/remote', ssl=client_ctx,
                                              open_timeout=5) as ws:
                    await ws.send('hello')
                    return await asyncio.wait_for(ws.recv(), 5)
            except Exception as e:  # noqa: BLE001 - the type is what the tests look at
                return e

    return asyncio.run(main())


def test_paired_nodes_talk_and_the_server_knows_who_called(nodes):
    s_cert, s_key = nodes['server']
    c_cert, c_key = nodes['client']
    answer = _run(server_context(s_cert, s_key, [c_cert]), client_context(c_cert, c_key, s_cert))
    assert answer == f'{fingerprint(c_cert)}:hello'


def test_client_with_unknown_certificate_is_refused_at_the_handshake(nodes):
    s_cert, s_key = nodes['server']
    c_cert, _ = nodes['client']
    r_cert, r_key = nodes['rogue-client']
    answer = _run(server_context(s_cert, s_key, [c_cert]), client_context(r_cert, r_key, s_cert))
    assert isinstance(answer, (ssl.SSLError, ConnectionError, OSError, websockets.exceptions.WebSocketException)), answer


def test_client_without_any_certificate_is_refused(nodes):
    s_cert, s_key = nodes['server']
    c_cert, _ = nodes['client']
    bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    bare.check_hostname = False
    bare.verify_mode = ssl.CERT_NONE        # even a client that does not care who the server is gets nowhere
    answer = _run(server_context(s_cert, s_key, [c_cert]), bare)
    assert isinstance(answer, Exception), answer


def test_server_presenting_another_certificate_is_refused_by_the_client(nodes):
    """The proxy / ssl-strip case: something in the middle answers with its own certificate"""
    s_cert, _ = nodes['server']
    c_cert, c_key = nodes['client']
    r_cert, r_key = nodes['rogue-server']
    # the rogue even accepts the client's real certificate: it is the client that must walk away
    answer = _run(server_context(r_cert, r_key, [c_cert]), client_context(c_cert, c_key, s_cert))
    assert isinstance(answer, ssl.SSLCertVerificationError), answer


def test_same_key_and_name_but_a_different_certificate_is_not_the_pinned_one(nodes):
    """Pinning is on the certificate itself, not on a name that anybody can put in their own"""
    s_cert, _ = nodes['server']
    c_cert, c_key = nodes['client']
    fake_cert, fake_key = generate_identity('server')       # same CN as the real one
    answer = _run(server_context(fake_cert, fake_key, [c_cert]), client_context(c_cert, c_key, s_cert))
    assert isinstance(answer, ssl.SSLCertVerificationError), answer


def test_nothing_pinned_means_nobody_gets_in(nodes):
    s_cert, s_key = nodes['server']
    c_cert, c_key = nodes['client']
    answer = _run(server_context(s_cert, s_key, []), client_context(c_cert, c_key, s_cert))
    assert isinstance(answer, Exception), answer
