__author__ = 'Alessandro Campolo (a13ssandr0)'
__version__ = '3.0.0'

from ipaddress import IPv4Address
from pathlib import Path

from pydantic import BaseModel, Field

# noinspection PyUnboundLocalVariable
__file__: Path = Path(__file__)

default_bind = '0.0.0.0'
default_port = 80
default_bind_secure = '0.0.0.0'
default_port_secure = 443
default_config_file = Path('/etc/unitotem/unitotem.conf')
envfile = Path('/etc/unitotem/unitotem.env')
certfile = '/etc/ssl/unitotem.pem'
keyfile = '/etc/ssl/unitotem.pem'

uploads_folder = __file__.joinpath('../../uploaded').resolve()
templates_folder = __file__.joinpath('../../templates').resolve()
static_folder = __file__.joinpath('../../static').resolve()

def_duration = 30


class Arguments(BaseModel):
    no_gui: bool = Field(False, description='Start UniTotem Manager without webview gui (for testing)')
    http_bind: IPv4Address = default_bind
    http_port: int = Field(default_port, gt=0, le=65525)
    https_bind: IPv4Address = default_bind_secure
    https_port: int = Field(default_port_secure, gt=0, le=65525)
    config: Path = default_config_file
