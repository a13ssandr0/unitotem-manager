__author__ = 'Alessandro Campolo (a13ssandr0)'
__version__ = '3.0.0'

from pathlib import Path

default_bind = '0.0.0.0'
default_port = 80
default_bind_secure = '0.0.0.0'
default_port_secure = 443
default_config_file = Path('/etc/unitotem/unitotem.conf')
envfile = Path('/etc/unitotem/unitotem.env')
certfile = '/etc/ssl/unitotem.pem'
keyfile = '/etc/ssl/unitotem.pem'

uploads_folder = Path(__file__).joinpath('../../uploaded').resolve()
static_folder = Path(__file__).joinpath('../../static').resolve()

def_duration = 30
