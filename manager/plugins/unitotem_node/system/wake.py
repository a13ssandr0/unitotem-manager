"""
When a suspended node should wake up: pure computation, no I/O (the RTC and logind are in `suspend.py`).

The wake time is a five-field cron schedule evaluated like the timers themselves: on the node's local wall clock, with
croniter. croniter is fed NAIVE local datetimes on purpose. Given a tz-aware datetime (ZoneInfo or dateutil's tzlocal)
croniter 6.0 gets the first occurrence after a DST change wrong by an hour (measured: `0 7 * * *` asked on the
evening before the spring change returned 06:00+02:00 instead of 07:00+02:00); with naive local times plus
`datetime.timestamp()` (mktime, DST-aware) the result is right (see the tests). Ambiguous (fold) and skipped (gap) local
times are resolved by `next_wake` below.
"""
from datetime import datetime

from croniter import CroniterBadDateError, CroniterBadCronError, croniter

# A wake-up closer than this is refused: the machine needs time to finish going to sleep, and an RTC alarm that is
# already in the past (or becomes so before the firmware looks at it) never fires, leaving the node asleep forever.
MIN_SLEEP = 120
# Longer than a weekend (Friday evening to Monday morning is 3 days) but far from "forgot it"; many RTCs also only
# hold a alarm of a month or less. A wake-up beyond this is almost certainly a mistake in the schedule.
MAX_SLEEP = 7 * 24 * 3600
# What is simulated when a suspend timer is saved: its runs over the next days, capped in number.
SIMULATED_DAYS = 8
SIMULATED_RUNS = 3000


class WakeError(ValueError):
    """The wake time cannot be used; the message says why, in words meant for the person who set the schedule."""


def _fmt(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M')


def next_occurrence(expression: str, after: float) -> float:
    """Epoch of the first occurrence of the schedule strictly after `after` (epoch seconds), in local time."""
    try:
        occurrences = croniter(expression, datetime.fromtimestamp(after))
        for _ in range(4):
            naive = occurrences.get_next(datetime)
            # A local time that happens twice (clocks going back) is tried at its first and second occurrence; one that
            # never happens (clocks going forward) is moved by the C library to the nearest valid instant.
            for fold in (0, 1):
                epoch = naive.replace(fold=fold).timestamp()
                if epoch > after:
                    return epoch
    except (CroniterBadDateError, CroniterBadCronError, ValueError, OverflowError) as exc:
        raise WakeError(f'The schedule {expression!r} never occurs ({exc})') from exc
    raise WakeError(f'The schedule {expression!r} has no occurrence after {_fmt(after)}')


def check_sleep(suspend_at: float, wake_at: float) -> float:
    """Validates the length of the sleep and returns the wake time; raises WakeError with the reason otherwise."""
    sleep = wake_at - suspend_at
    if sleep < MIN_SLEEP:
        raise WakeError(f'The wake-up at {_fmt(wake_at)} is only {max(0, int(sleep))} s after going to sleep: '
                        f'the node needs at least {MIN_SLEEP} s to sleep safely')
    if sleep > MAX_SLEEP:
        raise WakeError(f'The wake-up at {_fmt(wake_at)} is {sleep / 86400:.1f} days away: '
                        f'the longest sleep allowed is {MAX_SLEEP // 86400} days')
    return wake_at


def plan_wake(expression: str, suspend_at: float) -> float:
    """The wake time for a suspend at `suspend_at`: the next occurrence of the wake schedule, validated."""
    return check_sleep(suspend_at, next_occurrence(expression, suspend_at))


def check_pair(suspend_expression: str, wake_expression: str, now: float) -> float:
    """Dry run of a suspend timer: its suspends over the next days and, for each, its wake-up. Raises WakeError naming
    the first run that cannot work, so that the person sees the problem when saving rather than at night. Returns the
    longest sleep of the simulated runs, in seconds."""
    at, longest = now, 0.0
    for _ in range(SIMULATED_RUNS):
        at = next_occurrence(suspend_expression, at)
        if at > now + SIMULATED_DAYS * 86400:
            break
        try:
            longest = max(longest, plan_wake(wake_expression, at) - at)
        except WakeError as exc:
            raise WakeError(f'Suspending at {_fmt(at)} does not work: {exc}') from exc
    return longest


def parse_local(text: str) -> float:
    """Epoch of an ISO date-time the user typed. Without an offset it is local time of the node (not of the browser)."""
    try:
        when = datetime.fromisoformat(text.strip())
    except (ValueError, AttributeError) as exc:
        raise WakeError(f'{text!r} is not a date and time (expected YYYY-MM-DDTHH:MM)') from exc
    return when.timestamp()
