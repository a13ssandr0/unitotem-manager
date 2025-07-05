from ipaddress import IPv4Address
from pathlib import Path

from pydantic import ConfigDict, conint
from pydantic_settings import BaseSettings


# noinspection PyDataclass
class CommandLineArgs(BaseSettings, cli_parse_args=True, frozen=True):
    no_gui: bool = False
    bind: IPv4Address = IPv4Address("0.0.0.0")
    port: conint(gt=0, lt=65535) = 80
    bind_secure: IPv4Address = IPv4Address("0.0.0.0")
    port_secure: conint(gt=0, lt=65535) = 443
    assets_file: Path = Path('/etc/unitotem/assets.json')
    users_file: Path = Path('/etc/unitotem/users.json')
    remote_file: Path = Path('/etc/unitotem/remote.json')
    envfile: Path = Path('/etc/unitotem/unitotem.env')
    certfile: Path = Path('/etc/ssl/unitotem.pem')
    keyfile: Path = Path('/etc/ssl/unitotem.pem')

    uploads_folder: Path = Path(__file__).joinpath('../../../uploaded').resolve()
    static_folder: Path = Path(__file__).joinpath('../../../static').resolve()


cmdargs = CommandLineArgs()
