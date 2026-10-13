"""The rules of pairing: limits on strangers, the request queue, validation of what peers send, and the decisions a node
takes when somebody asks to adopt it or to be adopted."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import utils.constants as _constants
_constants.uploads_folder = Path(tempfile.mkdtemp(prefix='unitotem-uploads-'))

from utils.models import remote as remote_module  # noqa: E402
from utils.models.remote import RemoteManager  # noqa: E402
from utils.remote import identity as ident  # noqa: E402
from utils.remote import pairing  # noqa: E402
from utils.remote.pairing import PairingError, PairRequest, PairRequests, RateLimiter  # noqa: E402


def test_rate_limiter_counts_per_key_within_the_window():
    clock = [0.0]
    limiter = RateLimiter(3, 60, now=lambda: clock[0])
    assert [limiter.allow('a') for _ in range(4)] == [True, True, True, False]
    assert limiter.allow('b')                    # somebody else is not punished
    clock[0] = 59
    assert not limiter.allow('a')
    clock[0] = 61                                # the window moved on
    assert limiter.allow('a')


def _request(n, ip='10.0.0.5'):
    cert, _ = ident.generate_identity(f'c{n}')
    return PairRequest(node_id=ident.node_id(cert), name=f'c{n}', ip=ip, web_port=443, link_port=4443, cert=cert)


def test_requests_are_capped_per_address_and_in_total_and_expire():
    clock = [1000.0]
    queue = PairRequests(max_total=4, max_per_ip=2, ttl=600, now=lambda: clock[0])
    queue.add(_request(1)), queue.add(_request(2))
    with pytest.raises(PairingError):
        queue.add(_request(3))                       # a third from the same address
    queue.add(_request(4, ip='10.0.0.6')), queue.add(_request(5, ip='10.0.0.7'))
    with pytest.raises(PairingError):
        queue.add(_request(6, ip='10.0.0.8'))        # the queue is full
    clock[0] += 601                                  # everything expired
    assert queue.all() == []
    queue.add(_request(7))


def test_the_same_node_asking_again_replaces_its_request():
    queue = PairRequests(max_total=1, max_per_ip=1)
    first = _request(1)
    queue.add(first)
    again = PairRequest(**{**first.__dict__, 'ip': '10.0.0.9'})
    queue.add(again)                                 # not a second slot
    assert [r.ip for r in queue.all()] == ['10.0.0.9']
    assert queue.remove(first.node_id) is again and queue.get(first.node_id) is None


def test_validation_of_what_peers_send():
    cert, _ = ident.generate_identity()
    assert pairing.clean_cert(cert).startswith('-----BEGIN CERTIFICATE-----')
    for bad in (None, 5, '', 'junk', cert + 'x' * 10000, cert[:60]):
        with pytest.raises(PairingError):
            pairing.clean_cert(bad)
    assert pairing.clean_host(' 192.168.1.9 ') == '192.168.1.9'
    for bad in ('127.0.0.1', '0.0.0.0', '224.0.0.1', 'example.com', '::1', '999.1.1.1', ''):
        with pytest.raises(PairingError):
            pairing.clean_host(bad)
    assert pairing.clean_port('8443', 443) == 8443 and pairing.clean_port(0, 443) == 443 and pairing.clean_port('x', 4) == 4
    assert pairing.clean_name('a\x00b\n' + 'x' * 200) == 'ab' + 'x' * 62 and pairing.clean_name(None) == 'unnamed'


def test_hello_of_another_node():
    cert, _ = ident.generate_identity()
    hello = pairing.parse_hello({'v': 1, 'cert': cert, 'name': 'lobby', 'role': 'client', 'adoptable': True,
                                 'web_port': 443, 'link_port': 4443})
    assert hello['adoptable'] and hello['role'] == 'client' and hello['fingerprint'] == ident.fingerprint(cert)
    assert hello['node_id'] == ident.node_id(cert)
    for bad in (None, [], {'v': 2, 'cert': cert}, {'v': 1}, {'v': 1, 'cert': 'x'}):
        with pytest.raises(PairingError):
            pairing.parse_hello(bad)
    assert not pairing.parse_hello({'v': 1, 'cert': cert, 'adoptable': 'yes'})['adoptable']   # only a real true


# ── the decisions of a node ──────────────────────────────────────────────────

@pytest.fixture
def node(tmp_path, monkeypatch):
    from utils.remote import service as service_module
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setattr(remote_module, 'cmdargs', SimpleNamespace(remote_file=tmp_path / 'remote.json'))
    monkeypatch.setattr(RemoteManager, '_instance', None)
    svc = service_module.RemoteService()
    # nothing here starts the real machinery (listener, uplink, mDNS): only the decisions are under test
    async def nothing(*a, **k): return None
    monkeypatch.setattr(svc, 'apply_role', nothing)
    svc.rm.ensure_identity()
    yield svc
    RemoteManager._instance = None


def _adoption(node, server_cert, **over):
    body = {'cert': server_cert, 'name': 'main', 'web_port': 443, 'link_port': 4443,
            'expect_fingerprint': node.rm.fingerprint}
    body.update(over)
    return body


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_an_unpaired_client_accepts_adoption_from_anybody_and_pins_the_server(node):
    run(node.become_client())
    server_cert, _ = ident.generate_identity('server')
    answer = run(node.accept_adoption(_adoption(node, server_cert), '192.168.1.2'))
    rm = node.rm
    assert rm.is_paired_client and str(rm.server_ip) == '192.168.1.2' and rm.server_cert == pairing.clean_cert(server_cert)
    assert answer['cert'] == rm.identity_cert                    # the server pins the client with this
    with pytest.raises(PairingError):                            # and now it is not up for grabs any more
        run(node.accept_adoption(_adoption(node, ident.generate_identity()[0]), '192.168.1.99'))
    assert rm.server_fingerprint == ident.fingerprint(server_cert)


def test_a_server_is_not_adopted(node):
    with pytest.raises(PairingError):
        run(node.accept_adoption(_adoption(node, ident.generate_identity()[0]), '192.168.1.2'))


def test_adoption_meant_for_another_device_is_refused(node):
    run(node.become_client())
    with pytest.raises(PairingError):
        run(node.accept_adoption(_adoption(node, ident.generate_identity()[0], expect_fingerprint='0' * 64), '10.0.0.2'))
    assert not node.rm.is_paired_client


def test_adoption_must_come_with_a_certificate_and_from_an_ipv4_address(node):
    run(node.become_client())
    for body in (_adoption(node, 'junk'), _adoption(node, None)):
        with pytest.raises(PairingError):
            run(node.accept_adoption(body, '10.0.0.2'))
    with pytest.raises(PairingError):
        run(node.accept_adoption(_adoption(node, ident.generate_identity()[0]), '::1'))
    assert not node.rm.is_paired_client


def test_a_client_waiting_for_one_server_is_not_taken_by_another(node):
    run(node.become_client())
    wanted_cert, _ = ident.generate_identity('wanted')
    node.pending_server = {'ip': '10.0.0.2', 'port': 443, 'link_port': 4443, 'name': 'wanted', 'cert': wanted_cert,
                           'fingerprint': ident.fingerprint(wanted_cert), 'at': __import__('time').time()}
    with pytest.raises(PairingError):
        run(node.accept_adoption(_adoption(node, ident.generate_identity('other')[0]), '10.0.0.66'))
    assert not node.rm.is_paired_client
    run(node.accept_adoption(_adoption(node, wanted_cert), '10.0.0.2'))
    assert node.rm.is_paired_client


def test_a_waiting_client_is_free_again_after_the_request_expires(node):
    run(node.become_client())
    wanted_cert, _ = ident.generate_identity('wanted')
    node.pending_server = {'ip': '10.0.0.2', 'port': 443, 'link_port': 4443, 'name': 'w', 'cert': wanted_cert,
                           'fingerprint': ident.fingerprint(wanted_cert), 'at': 1.0}      # long ago
    run(node.accept_adoption(_adoption(node, ident.generate_identity('other')[0]), '10.0.0.66'))
    assert node.rm.is_paired_client


def test_a_server_queues_requests_and_never_pins_because_one_arrived(node):
    cert, _ = ident.generate_identity('client')
    body = {'cert': cert, 'name': 'lobby', 'web_port': 443, 'link_port': 4443}
    run(node.accept_request(body, '192.168.1.30'))
    assert [r.node_id for r in node.requests.all()] == [ident.node_id(cert)]
    assert node.rm.peers == {}                                    # nothing pinned: the administrator decides
    node.reject(ident.node_id(cert))
    assert node.requests.all() == []


def test_a_client_does_not_take_pairing_requests_and_a_server_does_not_take_its_own(node):
    cert, _ = ident.generate_identity('client')
    body = {'cert': cert, 'name': 'x', 'web_port': 443, 'link_port': 4443}
    with pytest.raises(PairingError):
        run(node.accept_request({**body, 'cert': node.rm.identity_cert}, '192.168.1.30'))
    run(node.become_client())
    with pytest.raises(PairingError):
        run(node.accept_request(body, '192.168.1.30'))


def test_a_node_with_clients_cannot_become_a_client_and_a_paired_client_cannot_become_a_server(node):
    node.rm.pin_client(ident.generate_identity()[0], 'c', None)
    with pytest.raises(PairingError):
        run(node.become_client())
    node.rm.peers.clear()
    run(node.become_client())
    run(node.accept_adoption(_adoption(node, ident.generate_identity()[0]), '10.0.0.2'))
    with pytest.raises(PairingError):
        run(node.become_server())
    run(node.forget_server())
    run(node.become_server())
    assert node.rm.role == 'server' and not node.rm.is_paired_client


def test_state_for_the_page_never_contains_a_key(node):
    run(node.become_client())
    run(node.accept_adoption(_adoption(node, ident.generate_identity()[0]), '10.0.0.2'))
    text = repr(node.state())
    assert 'PRIVATE' not in text and 'BEGIN' not in text
    assert node.state()['server']['ip'] == '10.0.0.2'
