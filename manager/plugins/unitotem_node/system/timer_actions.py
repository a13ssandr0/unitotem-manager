"""What each timer action does, and the loop that fires them at the start of every matching minute."""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from unitotem_node.models.timers import Timer, timer_manager

# After a resume, timers that fell due while the machine slept are still run if they are at most this many minutes old
CATCH_UP_MINUTES = 5


async def execute(timer: Timer):
    from unitotem_node.system.dbus_system import system_poweroff, system_reboot
    from unitotem_node.system.screens import control
    if timer.action == 'pwr':
        await system_poweroff()
    elif timer.action == 'reb':
        await system_reboot()
    elif timer.action == 'sus':
        from unitotem_node.system import suspend
        from unitotem_node.system.rtc import SuspendRefused
        try:
            await suspend.suspend_with_schedule(timer.wake, timer.uuid)
        except SuspendRefused as exc:
            logger.warning('Timer {}: not suspending: {}', timer.uuid, exc)
            from api.ws.endpoints import WS
            await WS.broadcast('Power/suspend_support', **await suspend.support())
    else:
        mechanism = await control.set_power(timer.output, timer.action.removeprefix('scr_'), timer.mechanism)
        logger.info('Timer {}: screen {} switched {} with {}', timer.uuid, timer.output,
                    timer.action.removeprefix('scr_'), mechanism)


def floor_minute(when: datetime) -> datetime:
    return when.replace(second=0, microsecond=0)


class MinuteClock:
    """Decides which minutes the timers loop has to evaluate, given that the wall clock is not a steady tick: the machine
    may sleep (suspend) or the clock may be stepped (NTP, a manual change).

    The policy, in one place:
    * a normal tick (the next minute): evaluate it;
    * the same minute again, or the clock went BACK: evaluate nothing and resynchronise. A timer is never run twice
      because the clock was set back (a reboot timer must not fire again), at the price that one due in the minute of the
      step may be skipped;
    * a jump FORWARD of more than a minute while the machine was NOT known to sleep (clock step, a stalled loop):
      evaluate only the current minute, the missed ones are not made up, like cron;
    * a jump forward because the machine WAS asleep (`resumed`): the current minute is evaluated, and so are the
      missed ones of the last CATCH_UP_MINUTES minutes, each once, oldest first, because a wake-up alarm lands on the
      minute a timer is due (a screen-on at 07:00 must not be lost because the resume finished at 07:00:40). Those
      caught-up minutes are flagged so that the `sus` action skips them: it never goes to sleep late by replay.
    Returns [(minute, caught_up)].
    """

    def __init__(self, now: datetime):
        self.last = floor_minute(now)

    def minutes(self, now: datetime, resumed: bool = False) -> list[tuple[datetime, bool]]:
        current = floor_minute(now)
        gap = round((current - self.last).total_seconds() / 60)
        self.last = current
        if gap <= 0:
            return []
        if gap == 1 or not resumed:
            return [(current, False)]
        missed = min(gap - 1, CATCH_UP_MINUTES)
        return [*((current - timedelta(minutes=m), True) for m in range(missed, 0, -1)), (current, False)]


async def run_due(minutes: list[tuple[datetime, bool]], executor=None):
    executor = executor or execute
    for minute, caught_up in minutes:
        for timer in timer_manager.due(minute):
            if caught_up and timer.action == 'sus':
                logger.info('Timer {} ({}) was due at {} while asleep: not suspending late', timer.uuid, timer.action, minute)
                continue
            logger.info('Running timer {} ({}){}', timer.uuid, timer.action, ' (caught up)' if caught_up else '')
            try:
                await executor(timer)
            except Exception:
                logger.exception('Timer {} failed', timer.uuid)


async def run_timers():
    """Sleeps to the next minute boundary (a wait on the clock, not a poll) and runs what is due. The wait ends early
    when the machine wakes up: asyncio sleeps on the MONOTONIC clock, which does not advance while suspended, so after a
    resume the pending sleep would still owe up to a minute of "awake" time. See MinuteClock for what is run."""
    from unitotem_node.system import suspend
    clock = MinuteClock(datetime.now())
    while not SHUTDOWN_EVENT.is_set():
        now = datetime.now()
        try:
            await asyncio.wait_for(suspend.resumed.wait(), 60 - now.second - now.microsecond / 1e6 + 0.05)
        except asyncio.TimeoutError:
            pass
        resumed = suspend.resumed.is_set()
        suspend.resumed.clear()
        await run_due(clock.minutes(datetime.now(), resumed))
