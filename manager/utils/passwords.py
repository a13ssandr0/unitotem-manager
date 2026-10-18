"""
What a new account may be called and which password it may have.

Always: the classic `passwd`/`useradd` rules (`validate_username`, `validate_basic`, `BASIC_RULES`). A stricter policy
(`validate_password`, `RULES`) is opt-in (security.json). The web UI shows the rules in force so that the person is told
before the server refuses.
"""
import re

MIN_LENGTH = 10
MAX_LENGTH = 128
# what useradd accepts on Debian (NAME_REGEX): the classic Unix account name
USERNAME_PATTERN = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
BASIC_MIN_LENGTH = 1

# What every default or lazy choice ends up being; compared after lower-casing and dropping digits and punctuation
_COMMON = frozenset({'password', 'passwort', 'admin', 'administrator', 'unitotem', 'changeme', 'qwerty', 'letmein',
                     'welcome', 'raspberry', 'kiosk', 'totem'})

# What is always required, like `passwd` itself: something, without characters that would break the way it is handed over
BASIC_RULES = [
    f'At most {MAX_LENGTH} characters',
    'No control characters',
]

# The stricter policy an administrator can switch on (security.json)
RULES = [
    f'At least {MIN_LENGTH} characters, at most {MAX_LENGTH}',
    'Different from the username',
    'Not a common word or a repetition of one character',
    'No control characters',
]


def validate_username(username: str) -> list[str]:
    """What is wrong with the username (an empty list means it is fine)."""
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        return ['The username must be 1 to 32 characters: lowercase letters, digits, dash and underscore, '
                'not starting with a digit or a dash']
    return []


def validate_basic(password: str) -> list[str]:
    """The rules of plain `passwd`: not empty, no control characters (a newline would end the line `chpasswd` reads), bounded."""
    if not isinstance(password, str):
        return ['The password must be text']
    problems = []
    if len(password) < BASIC_MIN_LENGTH:
        problems.append('The password must not be empty')
    if len(password) > MAX_LENGTH:
        problems.append(f'The password must be at most {MAX_LENGTH} characters long')
    if any(ord(c) < 32 or ord(c) == 127 for c in password):
        problems.append('The password must not contain control characters')
    return problems


def validate_password(password: str, username: str = '') -> list[str]:
    """What the STRICTER policy objects to (an empty list means it is fine); applied only when the node enforces it
    (`security.json`). The basic rules of `validate_basic` apply always."""
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
