"""Runtime product version (manager/utils/release.py). Every file is under tmp_path."""
from importlib.metadata import PackageNotFoundError

import pytest

from utils import release

EXAMPLE = '''# UniTotem release
NAME="UniTotem"
ID=unitotem
ID_LIKE=debian
VERSION_ID="2026.12"
VERSION="2026.12"
PRETTY_NAME="UniTotem 2026.12"
BUILD_ID="2026-12-01T10:00:00Z"
UNITOTEM_BASE="Debian GNU/Linux 13 (trixie)"
UNITOTEM_ARCH="arm64"
'''


@pytest.fixture(autouse=True)
def isolated_release(tmp_path, monkeypatch):
    # Default path and env override both point at a file that does not exist.
    absent = tmp_path / 'absent-release'
    monkeypatch.setattr(release, 'RELEASE_FILE', absent)
    monkeypatch.setenv('UNITOTEM_RELEASE_FILE', str(absent))
    release.reload()
    yield
    release.reload()


def write_release(tmp_path, text, name='unitotem-release'):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return path


def use_file(monkeypatch, path):
    monkeypatch.setenv('UNITOTEM_RELEASE_FILE', str(path))
    release.reload()


def test_full_example_is_parsed(tmp_path, monkeypatch):
    use_file(monkeypatch, write_release(tmp_path, EXAMPLE))
    assert release.info() == {
        'NAME': 'UniTotem',
        'ID': 'unitotem',
        'ID_LIKE': 'debian',
        'VERSION_ID': '2026.12',
        'VERSION': '2026.12',
        'PRETTY_NAME': 'UniTotem 2026.12',
        'BUILD_ID': '2026-12-01T10:00:00Z',
        'UNITOTEM_BASE': 'Debian GNU/Linux 13 (trixie)',
        'UNITOTEM_ARCH': 'arm64',
    }
    assert release.version() == '2026.12'
    assert release.pretty_name() == 'UniTotem 2026.12'


def test_quoting_comments_and_blank_lines(tmp_path, monkeypatch):
    text = (
        '# comment line\n'
        '\n'
        '   # indented comment\n'
        'PRETTY_NAME="UniTotem (kiosk) 2026.12"\n'
        'NAME="Uni \\"Totem\\""\n'
        "ID='unitotem'\n"
        'VERSION_ID="2026.12"\n'
        'BUILD_ID="a\\\\b \\$x \\`y\\`"\n'
        "UNITOTEM_BASE='Debian \\\"raw\\\" (trixie)'\n"
        'UNITOTEM_ARCH=arm64\n'
    )
    use_file(monkeypatch, write_release(tmp_path, text))
    data = release.info()
    assert data['PRETTY_NAME'] == 'UniTotem (kiosk) 2026.12'
    assert data['NAME'] == 'Uni "Totem"'
    assert data['ID'] == 'unitotem'
    assert data['BUILD_ID'] == 'a\\b $x `y`'
    # Single quotes are literal: no escape processing.
    assert data['UNITOTEM_BASE'] == 'Debian \\"raw\\" (trixie)'
    assert data['UNITOTEM_ARCH'] == 'arm64'
    assert all(not value.startswith('#') for value in data.values())


def test_missing_file_falls_back_to_package_version(monkeypatch):
    monkeypatch.setattr(release, '_package_version', lambda name: '2026.12~dev1')
    data = release.info()
    assert data == {
        'NAME': 'UniTotem',
        'ID': 'unitotem',
        'VERSION_ID': '2026.12~dev1',
        'VERSION': '2026.12~dev1',
        'PRETTY_NAME': 'UniTotem 2026.12~dev1',
    }
    assert release.version() == '2026.12~dev1'


def test_missing_package_falls_back_to_development(monkeypatch):
    def not_installed(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(release, '_package_version', not_installed)
    assert release.version() == 'development'
    assert release.pretty_name() == 'UniTotem development'


def test_env_override_is_read_at_load_time(tmp_path, monkeypatch):
    first = write_release(tmp_path, 'VERSION_ID="2026.12"\nPRETTY_NAME="UniTotem 2026.12"\n', 'first')
    second = write_release(tmp_path, 'VERSION_ID="2027.01"\nPRETTY_NAME="UniTotem 2027.01"\n', 'second')
    use_file(monkeypatch, first)
    assert release.version() == '2026.12'
    use_file(monkeypatch, second)
    assert release.version() == '2027.01'
    assert release.pretty_name() == 'UniTotem 2027.01'


def test_file_without_version_id_uses_fallback_for_version_fields(tmp_path, monkeypatch):
    text = 'NAME="UniTotem"\nID=unitotem\nPRETTY_NAME="Something else"\nVERSION="old"\n'
    use_file(monkeypatch, write_release(tmp_path, text))
    monkeypatch.setattr(release, '_package_version', lambda name: '2026.12~dev1')
    data = release.info()
    assert data['NAME'] == 'UniTotem'
    assert data['ID'] == 'unitotem'
    assert data['VERSION_ID'] == '2026.12~dev1'
    assert data['VERSION'] == '2026.12~dev1'
    assert data['PRETTY_NAME'] == 'UniTotem 2026.12~dev1'


def test_cache_until_reload(tmp_path, monkeypatch):
    path = write_release(tmp_path, 'VERSION_ID="2026.12"\n')
    use_file(monkeypatch, path)
    assert release.version() == '2026.12'
    path.write_text('VERSION_ID="2027.01"\n', encoding='utf-8')
    assert release.version() == '2026.12'
    release.reload()
    assert release.version() == '2027.01'


@pytest.mark.parametrize('value', ['2026.12', '2026.12.1', '2026.12~dev1', '2026.12~rc1', '2026.12.10'])
def test_is_valid_accepts(value):
    assert release.is_valid(value)


@pytest.mark.parametrize('value', [
    '3.0.0', '2026.1', '2026.12.1.2', '2026.12~dev', '2026.12~DEV1', ' 2026.12', '2026.12\n', '',
])
def test_is_valid_rejects(value):
    assert not release.is_valid(value)


@pytest.mark.parametrize('version, minimum, expected', [
    ('2026.12', '3.0.0', True),
    ('2026.12~dev1', '3.0.0', True),
    ('3.0.0~dev18', '3.0.0', False),
    ('3.0.0', '3.0.0', True),
    ('3.0', '3.0.0', True),
    ('3.0.0.0', '3.0.0', True),
    ('2.3.0', '3.0.0', False),
    ('2.9.0', '3.0.0', False),
    ('2.10.0', '2.9.0', True),
    ('3.0.0~dev18', '3.0.0~dev9', True),
    ('3.0.0~dev9', '3.0.0~dev18', False),
    ('3.0.0', '3.0.0~dev18', True),
    ('garbage', '3.0.0', False),
    ('3.0.0', 'garbage', False),
    ('', '3.0.0', False),
    (None, '3.0.0', False),
])
def test_version_at_least(version, minimum, expected):
    assert release.version_at_least(version, minimum) is expected
