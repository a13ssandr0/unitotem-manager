#!/usr/bin/env python3
"""
Run by cron for a screen timer: `screenctl.py <uuid>`.

The crontab line carries only the job's uuid; what to do (which output, on or off, with which mechanism) is read from
screen_control.json, where it was validated when the timer was created. It works whether or not the manager is
running, and sets DISPLAY/XAUTHORITY itself because cron's environment has neither.
"""
import asyncio
import os
import re
import sys

UUID_RE = re.compile(r'^[0-9a-f]{32}$')

if len(sys.argv) != 2 or not UUID_RE.match(sys.argv[1]):
    sys.exit('usage: screenctl.py <32 hex digits>')
uuid = sys.argv[1]
sys.argv = sys.argv[:1]   # the settings object of the manager parses the command line when imported

os.environ.setdefault('DISPLAY', ':0')
os.environ.setdefault('XAUTHORITY', '/run/unitotem-x11.auth')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.models.screen_control import screen_control  # noqa: E402
from utils.system.screens import control  # noqa: E402

timer = screen_control.timers.get(uuid)
if timer is None:
    sys.exit(f'no screen timer {uuid}')


async def main():
    try:
        mechanism = await control.set_power(timer.output, timer.state, timer.mechanism)
    except control.ScreenControlError as exc:
        sys.exit(f'screen {timer.output}: {exc}')
    print(f'screen {timer.output} switched {timer.state} with {mechanism}')


asyncio.run(main())
