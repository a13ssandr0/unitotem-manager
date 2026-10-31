"""
Pure priority resolution between the three power-control mechanisms - no
subprocess calls, no I/O; see the module docstring in __init__.py. The
capability model itself (SCREEN-CONTROL-PLAN.md §2):

    Operation      DDC/CI (>= 2.0)          CEC                       output
    Power off      0xD6 <- 04/05            --standby (0x36)          wlopm --off X (DPMS; the output stays enabled)
    Power on       0xD6 <- 01 (best-effort) --image-view-on + --active-source  wlopm --on X
    Input source   0x60                     not supported             -
    Volume/mute    0x62 / 0x8D              relative up/down, toggle  -

'auto' order (user decision 2026-10-25: DPMS first, "on that one we have full control"): the compositor's own output first, for
turning ON and OFF. A monitor switched off over DDC/CI, CEC or serial keeps its link, so the compositor cannot wake it and cannot even
tell it is off: when WE put it there (`commanded_off`, remembered across restarts) that mechanism is tried first to wake it.
Measured 2026-10-25 on an LG Ultrafine: 0xD6 <- 04 turns it off, the panel is dark while the output stays active and the 0xD6 read-back
stays 01; 0xD6 <- 01 alone does NOT wake it, 0xD6 <- 05 and then 01 does (see ddc_ctl.set_power). A DDC power-off is therefore never
the default: only when asked for explicitly or when the output mechanism is not available.
"""
from __future__ import annotations

from typing import Literal, Optional

Mechanism = Literal['cec', 'ddc', 'output', 'wol', 'serial']
Action = Literal['on', 'off']

# 'wol' and 'serial' are only ever available when somebody configured them for the output, which is the intent
# to use them: a monitor with Wake-on-LAN is woken by it first. WOL has no "off", it is simply never offered there.
_ORDER: dict[Action, tuple[Mechanism, ...]] = {
    'on':  ('wol', 'output', 'cec', 'ddc', 'serial'),
    'off': ('output', 'ddc', 'cec', 'serial'),
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


def candidates(available: set[Mechanism], action: Action, preferred: str = 'auto',
               commanded_off: Optional[str] = None) -> list[Mechanism]:
    """The mechanisms to try, in order. An explicit preference is the only candidate (no silent rerouting); on
    'auto' every available mechanism is a candidate, best first, so a failing one falls through to the next."""
    if preferred != 'auto':
        return [resolve_mechanism(available, action, preferred)]
    ordered = [m for m in _ORDER[action] if m in available]
    if action == 'on' and commanded_off in ordered:
        # we switched it off through a mechanism the compositor cannot see: only that one (or its siblings) wakes it
        ordered.remove(commanded_off)
        ordered.insert(0, commanded_off)  # type: ignore[arg-type]
    if not ordered:
        raise NoMechanismAvailable(f'no mechanism available for {action!r} on this output')
    return ordered
