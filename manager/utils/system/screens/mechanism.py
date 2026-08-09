"""
Pure priority resolution between the three power-control mechanisms - no
subprocess calls, no I/O; see the module docstring in __init__.py. The
capability model itself (SCREEN-CONTROL-PLAN.md §2):

    Operation      DDC/CI (>= 2.0)          CEC                       X
    Power off      0xD6 <- 04/05            --standby (0x36)          xrandr --output X --off
    Power on       0xD6 <- 01 (best-effort) --image-view-on + --active-source  xrandr --output X --auto
    Input source   0x60                     not supported             -
    Volume/mute    0x62 / 0x8D              relative up/down, toggle  -

'auto' order is CEC -> DDC -> X for turning ON (only CEC reliably wakes a
display that stopped responding on DDC once off - unverified on real hardware
so far, no CEC adapter has been available anywhere this was tested, but it is
the documented reason for CEC-first on power-on) and DDC -> CEC -> X for
turning OFF (DDC/CI reliably powers a monitor down - confirmed real, dev host:
0xD6 answered SNC values on every monitor tried).
"""
from __future__ import annotations

from typing import Literal

Mechanism = Literal['cec', 'ddc', 'x']
Action = Literal['on', 'off']

_ORDER: dict[Action, tuple[Mechanism, ...]] = {
    'on':  ('cec', 'ddc', 'x'),
    'off': ('ddc', 'cec', 'x'),
}


class NoMechanismAvailable(Exception):
    """Raised when no mechanism can do what was asked - either none of the
    three are available on this output at all, or the one explicitly
    requested is not."""


def resolve_mechanism(available: set[Mechanism], action: Action,
                       preferred: str = 'auto') -> Mechanism:
    """Pick which mechanism to use for `action` on an output whose available
    mechanisms are `available`.

    preferred='auto' (the default, and what the timers UI offers) picks the
    first of _ORDER[action] that is in `available`. Any other value is taken
    as an explicit request and used as-is if available, otherwise refused -
    an explicit 'ddc' request on a CEC-only output is a configuration error
    to surface, not something to silently reroute through CEC instead.
    """
    if preferred != 'auto':
        if preferred in available:
            return preferred  # type: ignore[return-value]
        raise NoMechanismAvailable(
            f'{preferred!r} was requested but is not available for this output '
            f'(available: {sorted(available) or "none"})')

    for mechanism in _ORDER[action]:
        if mechanism in available:
            return mechanism
    raise NoMechanismAvailable(
        f'no mechanism available for {action!r} on this output')
