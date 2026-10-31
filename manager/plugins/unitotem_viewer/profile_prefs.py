"""
Preferences of the browser profile that a kiosk fixes: no password manager, no autofill, no translation bubble, and the
permissions nobody could answer (location, notifications, popups) refused. They are ordinary Chromium preferences, written into
the profile's `Default/Preferences` before the browser starts (CEF reads enterprise policies only when the application names a
policy directory, which cefpython does not expose). Everything else in the file is left as the browser wrote it.
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 2 = block (CONTENT_SETTING_BLOCK)
KIOSK_PREFS = {
    'credentials_enable_service': False,
    'credentials_enable_autosignin': False,
    'profile': {
        'password_manager_enabled': False,
        'password_manager_leak_detection': False,
        'default_content_setting_values': {'notifications': 2, 'geolocation': 2, 'popups': 2},
    },
    'autofill': {'profile_enabled': False, 'credit_card_enabled': False},
    'translate': {'enabled': False},
}


def _merge(target: dict, wanted: dict):
    for key, value in wanted.items():
        if isinstance(value, dict):
            child = target.get(key)
            if not isinstance(child, dict):
                child = target[key] = {}
            _merge(child, value)
        else:
            target[key] = value


def apply(profile: Path, wanted: dict = KIOSK_PREFS) -> bool:
    """Puts the kiosk preferences into the profile. True when the file was changed. A preferences file that cannot be read is
    left alone (the browser rebuilds it) rather than replaced by one with only these keys."""
    path = Path(profile) / 'Default' / 'Preferences'
    try:
        current = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(current, dict):
            raise ValueError('not an object')
    except (OSError, ValueError) as exc:
        logger.warning('Browser preferences not touched, %s is unreadable: %s', path, exc)
        return False
    before = json.dumps(current, sort_keys=True)
    _merge(current, wanted)
    if json.dumps(current, sort_keys=True) == before:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.unitotem')
    temporary.write_text(json.dumps(current, separators=(',', ':')))
    os.replace(temporary, path)
    return True
