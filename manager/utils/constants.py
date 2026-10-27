__author__ = 'Alessandro Campolo (a13ssandr0)'
__version__ = '3.0.0'

# Names this particular start of the manager (not secret, only different every time). The page is served with it and the websocket
# repeats it on every connection: a page that holds another one was loaded from an earlier run and reloads itself.
import uuid
BOOT_ID = uuid.uuid4().hex[:12]

from pathlib import Path

default_bind = '0.0.0.0'
default_port = 80
default_bind_secure = '0.0.0.0'
default_port_secure = 443
default_config_file = Path('/etc/unitotem/unitotem.conf')
envfile = Path('/etc/unitotem/unitotem.env')
certfile = '/etc/ssl/unitotem.pem'
keyfile = '/etc/ssl/unitotem.pem'

# Uploads are runtime data: they must live on a writable, persistent path, never next to the code
# (/usr is read-only on the kiosk image). A pre-existing in-tree folder with content is still honoured
# so that packaged installs from before this change keep seeing their files.
_legacy_uploads = Path(__file__).joinpath('../../uploaded').resolve()
uploads_folder = Path('/var/lib/unitotem/uploaded')
if not uploads_folder.exists() and _legacy_uploads.is_dir() and any(_legacy_uploads.iterdir()):
    uploads_folder = _legacy_uploads
static_folder = Path(__file__).joinpath('../../static').resolve()

def_duration = 30
