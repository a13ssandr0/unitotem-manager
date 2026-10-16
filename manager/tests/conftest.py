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
