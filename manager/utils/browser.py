"""
The identity the embedded browser presents to the outside world.

Both the viewer and anything in the backend that fetches an asset on its
behalf - the media type probe in api/scheduler.py above all - have to introduce
themselves the same way. A probe that says something different is not answering
the question that matters, which is "what will the viewer get when it loads
this?": plenty of sites vary, or outright refuse, their answer by User-Agent.
Wikimedia returns 400 to a client it does not recognise, which was enough to
make an ordinary PNG look like an unclassifiable resource.
"""
import platform
from functools import lru_cache

from loguru import logger


@lru_cache(maxsize=1)
def user_agent() -> str:
    """
    Build the viewer's User-Agent from the CEF build actually installed.

    The Chromium version is read from the library rather than written down
    here, so it cannot drift when vendor/cefpython is bumped. Chrome itself
    reports only its major version and pads the rest with zeros, so the same
    reduction is applied here.

    webview/app.py feeds this back to CEF as its user_agent setting, which
    makes the two identical by construction instead of merely similar.
    """
    chrome = '0.0.0.0'
    try:
        from cefpython3 import cefpython as cef
        chrome = cef.GetVersion()['chrome_version']
    except (ImportError, KeyError, RuntimeError) as exc:
        # Headless installs may have no usable CEF at all; the format still has
        # to be a plausible browser or the probe is back to being refused.
        logger.warning('Cannot read the CEF version, User-Agent will be generic: {}', exc)
    major = chrome.split('.', 1)[0]
    machine = platform.machine()
    return (f'Mozilla/5.0 (X11; Linux {machine}) AppleWebKit/537.36 '
            f'(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36')
