from ipaddress import IPv4Address
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

from utils.constants import uploads_folder


# noinspection PyDataclass
class CommandLineArgs(BaseSettings, cli_parse_args=True, frozen=True):
    no_gui: bool = False   # headless mode: start only the backend, no local webview
    # Recovery and automation, for root on the machine: create (or reset the password of) an administrator and exit.
    # The password is read from the terminal, or from standard input when that is not a terminal; never from here.
    create_admin: Optional[str] = Field(default=None, validation_alias=AliasChoices('create_admin', 'create-admin'))
    bind: IPv4Address = IPv4Address("0.0.0.0")
    port: int = Field(default=80, gt=0, lt=65535)
    bind_secure: IPv4Address = IPv4Address("0.0.0.0")
    port_secure: int = Field(default=443, gt=0, lt=65535)
    # Mutual-TLS port of the link between a server and its clients (see utils/remote): certificates pinned at pairing time
    # are the only way in, unlike port_secure which any browser can reach
    bind_link: IPv4Address = IPv4Address("0.0.0.0")
    port_link: int = Field(default=4443, gt=0, lt=65535)
    assets_file: Path = Path('/etc/unitotem/assets.json')
    users_file: Path = Path('/etc/unitotem/users.json')
    remote_file: Path = Path('/etc/unitotem/remote.json')
    envfile: Path = Path('/etc/unitotem/unitotem.env')
    certfile: Path = Path('/etc/ssl/unitotem.pem')
    keyfile: Path = Path('/etc/ssl/unitotem.pem')

    uploads_folder: Path = uploads_folder
    static_folder: Path = Path(__file__).joinpath('../../../static').resolve()

logger.info("Parsing command line arguments")
cmdargs = CommandLineArgs()
