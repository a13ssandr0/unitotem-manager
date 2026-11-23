"""The archive records the release that wrote it: config.json 'version' is release.version()."""
import asyncio
import io
import json
import zipfile

import pytest

from utils import backup, release


@pytest.fixture
def no_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(backup, 'parts', lambda: {})
    rel = tmp_path / 'unitotem-release'
    rel.write_text('VERSION_ID="2026.12"\n')
    monkeypatch.setenv('UNITOTEM_RELEASE_FILE', str(rel))
    release.reload()
    yield
    release.reload()


def test_backup_config_json_carries_the_release_version(no_parts):
    from routers.backup import create_backup

    response = asyncio.run(create_backup(parts=None))
    with zipfile.ZipFile(io.BytesIO(response.body)) as zip_file:
        config = json.loads(zip_file.read('config.json'))
    assert config['version'] == release.version() == '2026.12'
    assert config['parts'] == {}
