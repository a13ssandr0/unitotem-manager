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

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def read_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def read_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()
