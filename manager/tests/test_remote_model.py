"""The pairing state: persistence, permissions of the file, backup and restore of the identity and of the pinned certificates."""
import asyncio
import json
import stat
from types import SimpleNamespace

import pytest

import tempfile
from pathlib import Path

# utils.backup imports the upload manager, which creates the uploads folder at import: not under /var/lib on a dev host
import utils.constants as _constants
_constants.uploads_folder = Path(tempfile.mkdtemp(prefix='unitotem-uploads-'))

from utils import backup  # noqa: E402
from utils.models import remote as remote_module  # noqa: E402
from utils.models.remote import RemoteManager
from utils.remote import identity as ident


@pytest.fixture
def rm(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setattr(remote_module, 'cmdargs', SimpleNamespace(remote_file=tmp_path / 'remote.json'))
    monkeypatch.setattr(RemoteManager, '_instance', None)
    yield RemoteManager.get_instance()
    RemoteManager._instance = None


def test_first_use_creates_a_stable_identity(rm, tmp_path):
    assert rm.identity_cert is None
    cert = rm.ensure_identity()
    assert rm.ensure_identity() == cert                       # never regenerated
    path = tmp_path / 'remote.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600           # it holds private keys
    RemoteManager._instance = None                            # a restart
    again = RemoteManager.get_instance()
    assert again.identity_cert == cert and again.node_id == ident.node_id(cert)


def test_the_same_object_is_shared(rm):
    assert RemoteManager.get_instance() is rm


def test_pin_and_forget_a_client(rm):
    cert, _ = ident.generate_identity('client')
    peer = rm.pin_client(cert, 'lobby', '192.168.1.9')
    assert peer.id == ident.node_id(cert) and peer.enabled
    assert rm.peer_by_fingerprint(ident.fingerprint(cert)) is peer
    assert rm.peer_by_fingerprint('0' * 64) is None
    assert rm.trusted_client_certs() == [cert]
    RemoteManager._instance = None
    assert list(RemoteManager.get_instance().peers) == [peer.id]     # persisted
    RemoteManager.get_instance().forget_client(peer.id)
    assert RemoteManager.get_instance().trusted_client_certs() == []


def test_backup_carries_identity_and_pinned_certificates_and_restore_brings_them_back(rm):
    cert, _ = ident.generate_identity('client')
    rm.pin_client(cert, 'lobby', '192.168.1.9')
    exported = json.loads(json.dumps(backup._export_remote()))       # what goes through the zip
    assert exported['identity_key'].startswith('-----BEGIN PRIVATE KEY') and len(exported['peers']) == 1
    node = rm.node_id

    # another box, restored from that archive
    remote_module.cmdargs.remote_file.unlink()
    RemoteManager._instance = None
    other = RemoteManager.get_instance()
    other.ensure_identity()
    assert other.node_id != node
    backup._restore_remote(exported, 1)
    restored = RemoteManager.get_instance()
    assert restored.node_id == node and restored.identity_key == exported['identity_key']
    assert restored.trusted_client_certs() == [cert]


def test_restore_refuses_a_key_that_does_not_match_its_certificate(rm):
    exported = backup._export_remote()
    exported['identity_key'] = ident.generate_identity()[1]
    before = rm.identity_cert
    with pytest.raises(ValueError):
        backup._restore_remote(exported, 1)
    assert RemoteManager.get_instance().identity_cert == before


def test_reset_makes_a_new_standalone_node(rm):
    old = rm.node_id
    rm.pin_client(ident.generate_identity()[0], 'x', None)
    backup._reset_remote()
    fresh = RemoteManager.get_instance()
    assert fresh.peers == {} and fresh.role == 'server' and fresh.server_cert is None
    assert fresh.node_id != old


def test_a_backup_from_before_the_identity_existed_restores_and_gets_one(rm):
    old_format = {'server_ip': None, 'server_port': 443, 'server_id': None, 'server_pubk': None, 'rsa_prik': None,
                  'clients': {}}
    backup._restore_remote(old_format, 1)
    fresh = RemoteManager.get_instance()
    assert fresh.identity_cert is None and fresh.role == 'server'
    assert fresh.node_id and fresh.identity_cert                      # generated when first needed


def test_an_unreadable_file_is_kept_aside_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'remote.json'
    path.write_text('{ this is not json')
    monkeypatch.setattr(remote_module, 'cmdargs', SimpleNamespace(remote_file=path))
    monkeypatch.setattr(RemoteManager, '_instance', None)
    fresh = RemoteManager.get_instance()
    assert fresh.role == 'server'
    assert (tmp_path / 'remote.json.corrupt').read_text() == '{ this is not json'
    RemoteManager._instance = None


def test_a_client_is_paired_only_with_a_pinned_server(rm):
    rm.role = 'client'
    assert rm.is_client and not rm.is_paired_client
    cert, _ = ident.generate_identity('server')
    rm.server_cert = cert
    assert rm.is_paired_client and rm.server_fingerprint == ident.fingerprint(cert)
