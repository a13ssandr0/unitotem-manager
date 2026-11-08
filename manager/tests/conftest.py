import sys
from pathlib import Path

import pytest

# utils.models.command_line.CommandLineArgs is a pydantic_settings BaseSettings
# with cli_parse_args=True, so simply IMPORTING it parses sys.argv - and under
# pytest that argv is pytest's own (test paths, -v, ...), which argparse
# rejects outright, aborting the whole test run before a single test module
# loads. Nothing under manager/api or manager/utils/models is safe to import
# without this: cron.py -> wsmanager.py -> models.remote -> models.command_line
# is one real chain that hits it. Sanitised here, in conftest.py, because it
# must happen before ANY test module's own imports run.
sys.argv = sys.argv[:1]

# Importing the asset models builds a module-level AssetsManager, whose timers need an event loop to exist
# (Python 3.14 no longer creates one implicitly). Same rule: before any test module's own imports.
import asyncio  # noqa: E402

asyncio.set_event_loop(asyncio.new_event_loop())

# The env file (hotspot_uuid, auth token) is read and written at import time: point it into a temp folder before any
# product module is imported, so no test reads or writes /etc/unitotem/unitotem.env.
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

from utils import constants as _constants  # noqa: E402
from utils.models.command_line import cmdargs as _cmdargs  # noqa: E402

_env_dir = Path(tempfile.mkdtemp(prefix='unitotem-test-env-'))
atexit.register(shutil.rmtree, _env_dir, True)
_cmdargs.__dict__['envfile'] = _env_dir / 'unitotem.env'
_constants.envfile = _env_dir / 'unitotem.env'
# Module-level singletons (assets, screen control, remote, users) load their file at import: none of them may read the real /etc/unitotem.
for _name in ('assets_file', 'users_file', 'remote_file'):
    _cmdargs.__dict__[_name] = _env_dir / f'{_name}.json'
# Uploads (and the transfer staging beside them) live under the temp folder too.
_constants.uploads_folder = _env_dir / 'uploaded'
_cmdargs.__dict__['uploads_folder'] = _constants.uploads_folder

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def read_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def read_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def _notifications_in_tmp(tmp_path, monkeypatch):
    """Code under test raises notifications (a refused suspend, a failed plugin): never into the real /var/lib/unitotem."""
    from utils.notifications import notifications
    monkeypatch.setattr(notifications, 'path', tmp_path / 'notifications.json')
    monkeypatch.setattr(notifications, '_items', [])
    monkeypatch.setattr(notifications, '_loaded', True)
    monkeypatch.setattr(notifications, '_loop', None)


@pytest.fixture(autouse=True)
def _users_file_in_tmp(tmp_path, monkeypatch):
    """users.json (and security.json beside it) never resolve to the real /etc/unitotem during a test."""
    from utils.models.command_line import cmdargs
    monkeypatch.setitem(cmdargs.__dict__, 'users_file', tmp_path / 'etc-users.json')
