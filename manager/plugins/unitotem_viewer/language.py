"""The language of the node: what pages are told to show in a window that has no language of its own."""
import os
import re
from pathlib import Path

_POSIX = re.compile(r'([A-Za-z]{2,3})(?:_([A-Za-z]{2}))?(?:[.@].*)?')
FALLBACK = 'en-US'


def _tag(value: str) -> str:
    """'it_IT.UTF-8' -> 'it-IT'; 'C', 'POSIX' and anything that is not a language -> ''."""
    match = _POSIX.fullmatch(value.strip())
    if not match or value.strip().upper().startswith(('C.', 'POSIX')) or value.strip() in ('C', 'POSIX'):
        return ''
    language, region = match.groups()
    return f'{language.lower()}-{region.upper()}' if region else language.lower()


def default_language(environ=None, locale_file: Path = Path('/etc/default/locale')) -> str:
    """The first of LC_ALL, LC_MESSAGES, LANG (then /etc/default/locale) that names a language, as a BCP 47 tag."""
    environ = os.environ if environ is None else environ
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        if tag := _tag(environ.get(name, '')):
            return tag
    try:
        for line in locale_file.read_text().splitlines():
            key, _, value = line.partition('=')
            if key.strip() in ('LC_ALL', 'LC_MESSAGES', 'LANG') and (tag := _tag(value.strip().strip('"\''))):
                return tag
    except OSError:
        pass
    return FALLBACK
