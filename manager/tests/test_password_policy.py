"""The password policy is opt-in for the passwords an administrator sets."""
import pytest

from utils import passwords
from utils.models.security import SecuritySettings


def test_it_is_off_by_default():
    assert SecuritySettings().enforce_password_policy is False


def test_the_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(SecuritySettings, '_filepath', staticmethod(lambda: tmp_path / 'security.json'))
    settings = SecuritySettings(enforce_password_policy=True)
    settings.save()
    assert SecuritySettings.load().enforce_password_policy is True
    assert SecuritySettings.load.__func__(SecuritySettings).enforce_password_policy is True


def test_a_missing_or_broken_file_means_not_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(SecuritySettings, '_filepath', staticmethod(lambda: tmp_path / 'security.json'))
    assert SecuritySettings.load().enforce_password_policy is False
    (tmp_path / 'security.json').write_text('{not json')
    assert SecuritySettings.load().enforce_password_policy is False


def test_the_policy_itself_still_refuses_weak_passwords():
    assert passwords.validate_password('short', 'bob')
    assert not passwords.validate_password('correct-horse-battery-9', 'bob')
