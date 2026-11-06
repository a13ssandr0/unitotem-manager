"""
What a new account may be called and which password it may have.

The password policy mirrors PAM (pam_pwquality) as configured for the system accounts, and is always applied.
`manager/tests/password_vectors.json` holds the PAM-observed results and is the shared spec. `POLICY` is the single
source of the numbers; `rules_spec()` gives the same rules to the web UI, which shows them before the server refuses.
"""
import re
import string
from typing import Any

# Numbers of the policy. Lengths: min_length and pam_max_bytes count UTF-8 bytes, max_length counts characters.
POLICY: dict[str, Any] = {
    'min_length': 8,
    'max_length': 128,
    'min_classes': 2,
    'max_repeat': 3,
    'username_min_len': 4,
    'pam_max_bytes': 511,
}

# What useradd accepts on Debian (NAME_REGEX): the classic Unix account name
USERNAME_PATTERN = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')

# Character classes, ASCII only: anything outside the three ranges is "other" (symbols, spaces, non-ASCII)
_LOWER = re.compile(r'[a-z]')
_UPPER = re.compile(r'[A-Z]')
_DIGIT = re.compile(r'[0-9]')
_OTHER = re.compile(r'[^a-zA-Z0-9]')
_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# Rule ids in the order PAM checks them; the first failing id is the only one reported
_ORDER = ('max_length', 'control', 'min_length', 'classes', 'repeat', 'username')

_MESSAGES = {
    'type': 'The password must be text',
    'max_length': f'The password must be at most {POLICY["max_length"]} characters long',
    'control': 'The password must not contain control characters',
    'min_length': f'The password is shorter than {POLICY["min_length"]} characters',
    'classes': f'The password contains less than {POLICY["min_classes"]} character classes',
    'repeat': f'The password contains more than {POLICY["max_repeat"]} same characters consecutively',
    'username': 'The password contains the user name in some form',
}

# Checklist rows shown to the person, in display order (control characters are not a row)
_RULE_TEXTS = (
    ('min_length', f'At least {POLICY["min_length"]} characters'),
    ('classes', f'At least {POLICY["min_classes"]} of: lowercase, uppercase, digits, symbols'),
    ('repeat', f'No character more than {POLICY["max_repeat"]} times in a row'),
    ('username', 'Does not contain the username'),
    ('max_length', f'At most {POLICY["max_length"]} characters'),
)


def validate_username(username: str) -> list[str]:
    """What is wrong with the username (an empty list means it is fine)."""
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        return ['The username must be 1 to 32 characters: lowercase letters, digits, dash and underscore, '
                'not starting with a digit or a dash']
    return []


def failed_ids(password: str, username: str = '') -> list[str]:
    """The id of the first rule the password breaks, in PAM order; an empty list means the password is fine."""
    if not isinstance(password, str):
        return ['type']
    # Lone surrogates are not valid UTF-8: they count as one replacement byte
    data = password.encode('utf-8', errors='replace')
    folded = password.translate(_ASCII_FOLD)
    failed = set()
    if len(password) > POLICY['max_length'] or len(data) > POLICY['pam_max_bytes']:
        failed.add('max_length')
    elif any(ord(c) < 32 or ord(c) == 127 for c in password):
        failed.add('control')
    elif len(data) < POLICY['min_length']:
        failed.add('min_length')
    elif sum(bool(p.search(password)) for p in (_LOWER, _UPPER, _DIGIT, _OTHER)) < POLICY['min_classes']:
        failed.add('classes')
    elif re.search(rb'(.)\1{%d}' % POLICY['max_repeat'], data, re.DOTALL):
        failed.add('repeat')
    elif (isinstance(username, str) and len(username) >= POLICY['username_min_len']
          and (username.translate(_ASCII_FOLD) in folded or username.translate(_ASCII_FOLD)[::-1] in folded)):
        failed.add('username')
    return [rule for rule in _ORDER if rule in failed]


def validate_password(password: str, username: str = '') -> list[str]:
    """What is wrong with the password, in words (an empty list means it is fine)."""
    return [_MESSAGES[rule] for rule in failed_ids(password, username)]


def rules_spec() -> dict[str, Any]:
    """The policy numbers and the checklist rows, JSON-serialisable, for the web UI."""
    return {
        **POLICY,
        'rules': [{'id': rule_id, 'text': text} for rule_id, text in _RULE_TEXTS],
    }
