__author__ = 'Alessandro Campolo (a13ssandr0)'
__version__ = '3.0.0'

from pathlib import Path

# noinspection PyUnboundLocalVariable
__file__: Path = Path(__file__)

default_port = 80
default_port_secure = 443
default_bind = f'0.0.0.0:{default_port}'
default_bind_secure = f'0.0.0.0:{default_port_secure}'
default_config_file = Path('/etc/unitotem/unitotem.conf')
envfile = Path('/etc/unitotem/unitotem.env')
certfile = '/etc/ssl/unitotem.pem'
keyfile = '/etc/ssl/unitotem.pem'

uploads_folder = __file__.joinpath('../../uploaded').resolve()
templates_folder = __file__.joinpath('../../templates').resolve()
static_folder = __file__.joinpath('../../static').resolve()

def_duration = 30
