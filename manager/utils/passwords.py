"""
What a new administrator account may be called and which password it may have.

Used by the first-access setup (utils/claim.py) and by `unitotem-manager --create-admin`; the web UI shows the same
rules (`RULES`) so that the person is told before the server refuses.
"""
import re

MIN_LENGTH = 10
MAX_LENGTH = 128
USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$')

# What every default or lazy choice ends up being; compared after lower-casing and dropping digits and punctuation
_COMMON = frozenset({'password', 'passwort', 'admin', 'administrator', 'unitotem', 'changeme', 'qwerty', 'letmein',
                     'welcome', 'raspberry', 'kiosk', 'totem'})

RULES = [
    f'At least {MIN_LENGTH} characters, at most {MAX_LENGTH}',
    'Different from the username',
    'Not a common word or a repetition of one character',
    'No control characters',
]


def validate_username(username: str) -> list[str]:
    """What is wrong with the username (an empty list means it is fine)."""
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        return ['The username must be 1 to 32 characters: letters, digits, dot, dash and underscore, '
                'starting with a letter or a digit']
    return []


def validate_password(password: str, username: str = '') -> list[str]:
    """What is wrong with the password (an empty list means it is fine). The server enforces this, always."""
    if not isinstance(password, str):
        return ['The password must be text']
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f'The password must be at least {MIN_LENGTH} characters long')
    if len(password) > MAX_LENGTH:
        problems.append(f'The password must be at most {MAX_LENGTH} characters long')
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        problems.append('The password must not contain control characters')
    if username and password.casefold() == username.casefold():
        problems.append('The password must be different from the username')
    if password and len(set(password)) == 1:
        problems.append('The password must not be a single repeated character')
    letters = re.sub(r'[^a-z]', '', password.casefold())
    if letters in _COMMON:
        problems.append('The password is too common')
    return problems
