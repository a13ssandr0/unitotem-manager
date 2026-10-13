"""
The pairing state of this node, persisted in remote.json (and part of the backup).

A node always owns an *identity* (certificate + key, generated once, see utils.remote.identity). A node is either a
server (the default; standalone is a server with no client) or a client. Pairing pins the other party's certificate:
a server keeps the certificate of every paired client (`peers`), a client keeps the one of its server.

Backward compatibility: the previous version only reads `server_ip`, `server_port`, `rsa_prik` and ignores the rest, and
this one ignores the `clients` / `server_pubk` it used to write (they were never usable: nothing validated them).
"""
import json
import os
import time
from ipaddress import IPv4Address
from threading import RLock
from typing import Annotated, ClassVar, Literal, Optional

from Cryptodome.PublicKey import RSA
from loguru import logger
from pydantic import BaseModel, Field, field_serializer, field_validator

from utils import constants as const
from utils.models.command_line import cmdargs
from utils.remote import identity as ident

# state of the lazy RSA signing-key generation (see RemoteManager.get_signing_key)
_keygen_lock = RLock()
_keygen_running = False


class Peer(BaseModel, validate_assignment=True):
    """A client paired with this server"""
    id: str                          # node id of the client, derived from its certificate
    name: str                        # its hostname, as of the last time it told
    cert: str                        # PEM, pinned
    ip: Optional[str] = None         # where it was last seen: the address its configuration page is reached at
    port: int = const.default_port_secure
    enabled: bool = True             # a disabled client stays paired but is refused until enabled again
    paired_at: float = Field(default_factory=time.time)

    @property
    def fingerprint(self) -> str:
        return ident.fingerprint(self.cert)


class RemoteManager(BaseModel, validate_assignment=True, arbitrary_types_allowed=True):
    role: Literal['server', 'client'] = 'server'

    # the identity of this node: generated at first use, never changed (a restore replaces it as a whole)
    identity_cert: Optional[str] = None
    identity_key: Optional[str] = None

    # client side: the server this node belongs to
    server_ip: 'Optional[IPv4Address]' = None
    server_port: 'Annotated[Optional[int], Field(gt=0, le=65535)]' = const.default_port_secure   # its web port
    server_link_port: 'Annotated[Optional[int], Field(gt=0, le=65535)]' = None                   # its mutual-TLS port
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    server_cert: Optional[str] = None  # PEM, pinned

    # server side: the clients paired with this node
    peers: 'dict[str, Peer]' = Field(default_factory=dict)

    # signs the commands sent to the local viewer (not a security boundary between nodes: the link is pinned TLS)
    rsa_prik: 'Optional[RSA.RsaKey]' = None

    # the live object: remote.json is read once, every part of the manager shares this one
    _instance: ClassVar[Optional['RemoteManager']] = None

    # ── model plumbing ────────────────────────────────────────────────────

    # noinspection PyNestedDecorators
    @field_validator('rsa_prik', mode='before')
    @classmethod
    def validate_rsa_key(cls, key):
        if key:
            return key if isinstance(key, RSA.RsaKey) else RSA.import_key(key)
        return None

    @field_serializer('rsa_prik')
    def serialize_rsa_key(self, key: 'RSA.RsaKey'):
        return key.export_key().decode() if key else None

    # ── identity ──────────────────────────────────────────────────────────

    def ensure_identity(self) -> str:
        """The certificate of this node, generated and saved the first time it is asked for."""
        with _keygen_lock:
            if not (self.identity_cert and self.identity_key and ident.check_pair(self.identity_cert, self.identity_key)):
                if self.identity_cert or self.identity_key:
                    logger.error('The identity of this node is damaged, generating a new one: paired nodes must be paired again')
                cert, key = ident.generate_identity()
                self.identity_cert, self.identity_key = cert, key
                self.save()
                logger.success('Generated the identity of this node ({})', self.node_id)
            return self.identity_cert

    @property
    def node_id(self) -> str:
        return ident.node_id(self.ensure_identity())

    @property
    def fingerprint(self) -> str:
        return ident.fingerprint(self.ensure_identity())

    @property
    def server_fingerprint(self) -> Optional[str]:
        return ident.fingerprint(self.server_cert) if self.server_cert else None

    @property
    def is_client(self) -> bool:
        return self.role == 'client'

    @property
    def is_paired_client(self) -> bool:
        """A client with a server: its viewer is driven by that server"""
        return self.role == 'client' and self.server_cert is not None

    # ── peers ─────────────────────────────────────────────────────────────

    def peer_by_fingerprint(self, fp: str) -> Optional[Peer]:
        for peer in self.peers.values():
            if peer.fingerprint == fp:
                return peer
        return None

    def trusted_client_certs(self) -> list[str]:
        return [peer.cert for peer in self.peers.values()]

    def pin_client(self, cert: str, name: str, ip: Optional[str], port: int = const.default_port_secure) -> Peer:
        """Remember a client's certificate: from now on its connection is accepted."""
        peer_id = ident.node_id(cert)
        peer = Peer(id=peer_id, name=name, cert=cert, ip=ip, port=port)
        self.peers[peer_id] = peer
        self.save()
        return peer

    def forget_client(self, peer_id: str):
        self.peers.pop(peer_id, None)
        self.save()

    # ── persistence ───────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> 'RemoteManager':
        """The one live object. remote.json is read the first time (a missing file means a node that has never been
        paired: the defaults) and every later call returns the same instance, so a change is seen everywhere."""
        with _keygen_lock:
            if cls._instance is None:
                try:
                    with open(cmdargs.remote_file) as f:
                        cls._instance = cls(**json.load(f))
                except FileNotFoundError:
                    cls._instance = cls()
                except ValueError as e:
                    # never overwrite what cannot be read: it may be the only copy of the keys of a paired node
                    broken = cmdargs.remote_file.with_name(cmdargs.remote_file.name + '.corrupt')
                    logger.error('Cannot read {} ({}), kept as {} and starting unpaired', cmdargs.remote_file, e, broken)
                    cmdargs.remote_file.replace(broken)
                    cls._instance = cls()
            return cls._instance

    @classmethod
    def restore(cls, data: dict) -> 'RemoteManager':
        """Replace the whole state (restore of a backup)."""
        with _keygen_lock:
            new = cls(**data)
            if new.identity_cert and new.identity_key and not ident.check_pair(new.identity_cert, new.identity_key):
                raise ValueError('The certificate and the key in this backup do not belong together')
            cls._instance = new
            new.save()
            return new

    @classmethod
    def reset(cls) -> 'RemoteManager':
        """Forget every pairing and the identity: the node is a new one (a standalone server)."""
        return cls.restore({})

    @classmethod
    def get_signing_key(cls) -> RSA.RsaKey:
        """RSA private key used to sign the commands for the viewer, generated and persisted on first use: not needed
        for basic operation.

        Generation is started in a worker thread right after startup (main.py); the lock also covers the synchronous
        fallback in WSManager.prepare_message so exactly one key is ever generated, concurrent callers wait for it."""
        global _keygen_running
        with _keygen_lock:
            instance = cls.get_instance()
            if instance.rsa_prik is None:
                _keygen_running = True
                try:
                    logger.info('Generating RSA signing key')
                    instance.rsa_prik = RSA.generate(4096)
                    instance.save()
                    logger.success('RSA signing key saved to {}', cmdargs.remote_file)
                finally:
                    _keygen_running = False
            return instance.rsa_prik

    @classmethod
    def signing_key_status(cls) -> str:
        """'missing' | 'generating' | 'ready'"""
        if _keygen_running:
            return 'generating'
        return 'ready' if cls.get_instance().rsa_prik else 'missing'

    def save(self):
        """Atomic, and readable by root only: the file holds the private keys of this node."""
        with _keygen_lock:
            path = cmdargs.remote_file
            tmp = path.with_name(path.name + '.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(self.model_dump(mode='json'), f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)


remote_manager = RemoteManager.get_instance()
