import json
from ipaddress import IPv4Address
from secrets import token_hex
from typing import Optional

from Crypto.PublicKey import RSA
from pydantic import BaseModel, Field, field_serializer, field_validator

from utils import constants as const
from utils.models.command_line import cmdargs


class Client(BaseModel, validate_assignment=True, arbitrary_types_allowed=True):
    public_key: RSA.RsaKey
    ip_address: IPv4Address


class RemoteManager(BaseModel, validate_assignment=True, arbitrary_types_allowed=True):
    server_ip: Optional[IPv4Address] = None
    server_port: Optional[int] = Field(const.default_port_secure, gt=0, le=65535)
    server_id: Optional[str] = None
    server_pubk: Optional[RSA.RsaKey] = None
    rsa_prik: RSA.RsaKey = Field(default_factory=lambda: RSA.generate(4096))
    clients: dict[str, Client] = Field(default_factory=dict)

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
    def serialize_rsa_key(self, key: RSA.RsaKey):
        return key.export_key().decode() if key else None

    def associate_client(self, pub_key: str, ip: str):
        _id = token_hex()
        self.clients[_id] = Client(public_key=RSA.import_key(pub_key), ip_address=IPv4Address(ip))
        self.save()
        return _id

    def load(self):
        with open(cmdargs.remote_file) as f:
            self.__init__(**json.load(f))

    def save(self):
        with open(cmdargs.remote_file, 'w') as f:
            json.dump(self.model_dump(), f, indent=4)


remote_manager = RemoteManager()