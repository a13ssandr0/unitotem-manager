"""The node security settings: an old security.json with the removed password-policy key still loads."""
import json

import pytest

from utils.models.command_line import cmdargs
from utils.models.security import SecuritySettings


@pytest.fixture
def settings_dir(monkeypatch, tmp_path):
    monkeypatch.setitem(cmdargs.__dict__, 'users_file', tmp_path / 'users.json')
    return tmp_path


def test_an_old_file_with_the_removed_key_loads(settings_dir):
    (settings_dir / 'security.json').write_text(
        json.dumps({'enforce_password_policy': True, 'show_login_users': False}))
    settings = SecuritySettings.load()
    assert settings.show_login_users is False
    assert not hasattr(settings, 'enforce_password_policy')


def test_the_removed_key_is_dropped_on_save(settings_dir):
    (settings_dir / 'security.json').write_text(json.dumps({'enforce_password_policy': True}))
    settings = SecuritySettings.load()
    assert settings.show_login_users is True
    settings.save()
    assert json.loads((settings_dir / 'security.json').read_text()) == {'show_login_users': True}


def test_a_missing_file_shows_the_users(settings_dir):
    assert SecuritySettings.load().show_login_users is True
