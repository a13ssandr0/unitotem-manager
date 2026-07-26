import json
from ipaddress import IPv4Address
from secrets import token_hex
from threading import Lock
from typing import Annotated, Optional

from Crypto.PublicKey import RSA
from pydantic import BaseModel, Field, field_serializer, field_validator

from utils import constants as const
from utils.models.command_line import cmdargs

from time import time
from loguru import logger
tic = time()

# state of the lazy RSA signing-key generation (see RemoteManager.get_signing_key)
_keygen_lock = Lock()
_keygen_running = False

class Client(BaseModel, defer_build=True, validate_assignment=True, arbitrary_types_allowed=True):
    public_key: RSA.RsaKey
    ip_address: IPv4Address


class RemoteManager(BaseModel, validate_assignment=True, arbitrary_types_allowed=True):
    server_ip: 'Optional[IPv4Address]' = None
    server_port: 'Annotated[Optional[int], Field(gt=0, le=65535)]' = const.default_port_secure
    server_id: 'Optional[str]' = None
    server_pubk: 'Optional[RSA.RsaKey]' = None
    rsa_prik: 'Optional[RSA.RsaKey]' = None #Field(default_factory=lambda: RSA.generate(4096))
    clients: 'dict[str, Client]' = Field(default_factory=dict)

    @property
    def clients_list(self):
        return [(k, v.model_dump()) for k,v in self.clients.items()]

    # noinspection PyNestedDecorators
    @field_validator('rsa_prik', 'server_pubk', mode='before')
    @classmethod
    def validate_rsa_key(cls, key):
        if key:
            if isinstance(key, RSA.RsaKey):
                return key
            else:
                return RSA.import_key(key)
        else:
            return None

    @field_serializer('rsa_prik', 'server_pubk')
    def serialize_rsa_key(self, key: 'RSA.RsaKey'):
        return key.export_key().decode() if key else None

    def associate_client(self, pub_key: str, ip: str):
        _id = token_hex()
        self.clients[_id] = Client(public_key=RSA.import_key(pub_key), ip_address=IPv4Address(ip))
        self.save()
        return _id

    @classmethod
    def get_instance(cls):
        with open(cmdargs.remote_file) as f:
            return cls(**json.load(f))

    @classmethod
    def get_signing_key(cls) -> RSA.RsaKey:
        """RSA private key used to sign webview commands, generated and
        persisted on first use: not needed for basic operation.

        Generation is started in a worker thread right after startup (main.py);
        the lock also covers the synchronous fallback in WSManager.prepare_message
        so exactly one key is ever generated, concurrent callers wait for it."""
        global _keygen_running
        with _keygen_lock:
            try:
                instance = cls.get_instance()
            except FileNotFoundError:
                instance = cls()
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
        try:
            return 'ready' if cls.get_instance().rsa_prik else 'missing'
        except FileNotFoundError:
            return 'missing'

    def save(self):
        with open(cmdargs.remote_file, 'w') as f:
            json.dump(self.model_dump(), f, indent=4)

#FIXME takes a lot of time to initialize
remote_manager = RemoteManager()
logger.trace("Took {:1.6f} seconds", time()-tic)