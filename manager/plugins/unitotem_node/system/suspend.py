"""
Suspend to RAM with a scheduled wake-up, and what happens around sleep.

Going to sleep: every refusal is decided BEFORE anything is touched (logind says the machine can suspend, an RTC can wake
it, the wake time is sane), then the alarm is written through sysfs (rtc.py) and the machine is put to sleep through
logind's own `Suspend` (D-Bus), never by running `rtcwake` or `systemctl`. If the suspend request fails the alarm is
cleared again.

Around sleep: logind's `PrepareForSleep` signal is the event (no polling): `False` means the machine just woke up. Then
the alarm is cleared, the timers loop is told (it sleeps on the monotonic clock, which does NOT run during a suspend, so
without the nudge it would wake up to a minute late, see timer_actions.py) and the screens are woken like at start-up.
No inhibitor lock is taken: nothing has to be done before sleeping that could not be skipped.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional

import sdbus
from loguru import logger

from utils.notifications import notifications
from unitotem_node.system import dbus_system, rtc, wake
from unitotem_node.system.rtc import SuspendRefused

# Set when the machine has just woken up; consumed by the timers loop.
resumed = asyncio.Event()

_lock = asyncio.Lock()
_armed: Optional[rtc.Rtc] = None
last_refusal: Optional[dict] = None         # {'reason': str, 'when': epoch, 'timer': uuid or None}, for the web UI
_tasks: set[asyncio.Task] = set()

# One notification for "the suspend timer does not work": raised when a TIMER's suspend is refused (somebody asking by
# hand gets the reason in their own dialog) or skipped, resolved by the next suspend that works.
NOTIFY_KEY = 'suspend.problem'


def notify_problem(title: str, reason: str):
    notifications.notify(NOTIFY_KEY, 'warning', title, reason, source='timers', audience='admin',
                         link='/settings/timers')


def notify_refused(reason: str, timer: Optional[str]):
    if timer is not None:
        notify_problem('The node did not go to sleep', reason)


async def _check_logind():
    try:
        answer = await dbus_system.can_suspend()
    except Exception as exc:
        raise SuspendRefused(f'Cannot ask the login manager (logind) whether the machine can suspend: {exc}') from exc
    if answer != 'yes':
        why = {'na': 'this machine or its kernel does not support suspend',
               'no': 'the system policy forbids it',
               'challenge': 'it would need an authentication that UniTotem cannot give'}.get(answer, f'answer {answer!r}')
        raise SuspendRefused(f'The machine cannot suspend: {why} (logind CanSuspend: {answer})')


async def check() -> rtc.Rtc:
    """Everything that can be known before suspending; returns the RTC to use or raises SuspendRefused."""
    await _check_logind()
    return rtc.find_rtc()


async def support() -> dict:
    """What the web UI shows before offering the action: {'available': bool, 'reason': str or None, 'rtc': name}."""
    info = {'rtc_local': rtc.rtc_is_local(), 'min_sleep': wake.MIN_SLEEP, 'max_sleep': wake.MAX_SLEEP,
            'last_refusal': last_refusal}
    try:
        found = await check()
        return {**info, 'available': True, 'reason': None, 'rtc': found.name}
    except SuspendRefused as exc:
        return {**info, 'available': False, 'reason': str(exc), 'rtc': None}


async def suspend_until(wake_epoch: float, timer: Optional[str] = None):
    """Arms the wake-up for `wake_epoch` and suspends. Raises SuspendRefused (and remembers why) without side effects."""
    global _armed, last_refusal
    if _lock.locked():
        raise SuspendRefused('A suspend is already in progress')
    async with _lock:
        try:
            now = time.time()
            wake.check_sleep(now, wake_epoch)
            found = await check()
            rtc.arm(found, wake_epoch)
        except (SuspendRefused, wake.WakeError) as exc:
            last_refusal = {'reason': str(exc), 'when': time.time(), 'timer': timer}
            notify_refused(str(exc), timer)
            raise SuspendRefused(str(exc)) from exc
        _armed = found
        last_refusal = None
        notifications.resolve(NOTIFY_KEY)
        logger.info('Suspending, wake-up armed on {} for {} ({} s from now)', found.name,
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(wake_epoch)), int(wake_epoch - now))
        try:
            await dbus_system.system_suspend()
        except Exception as exc:
            rtc.clear_quietly(found)
            _armed = None
            last_refusal = {'reason': f'The suspend request failed: {exc}', 'when': time.time(), 'timer': timer}
            notify_refused(last_refusal['reason'], timer)
            raise SuspendRefused(f'The suspend request failed: {exc}') from exc


async def suspend_with_schedule(wake_expression: str, timer: Optional[str] = None):
    """The suspend timer: wake at the next occurrence of the schedule after now."""
    global last_refusal
    try:
        epoch = wake.plan_wake(wake_expression, time.time())
    except wake.WakeError as exc:
        last_refusal = {'reason': str(exc), 'when': time.time(), 'timer': timer}
        notify_refused(str(exc), timer)
        raise SuspendRefused(str(exc)) from exc
    await suspend_until(epoch, timer)


# ── around sleep ─────────────────────────────────────────────────────────────

async def on_prepare_for_sleep(starting: bool, wake_screens: Optional[Callable[[], Awaitable[None]]] = None):
    global _armed
    if starting:
        logger.info('The system is going to sleep')
        return
    logger.info('The system woke up')
    if _armed is not None:
        rtc.clear_quietly(_armed)       # woken by someone else before the alarm: do not leave it pending
        _armed = None
    resumed.set()
    if wake_screens is not None:
        # up to minutes (a panel can take the best part of one to show its source): never block the signal loop on it
        task = asyncio.get_running_loop().create_task(wake_screens(), name='wake_screens_after_resume')
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def wake_screens_after_resume():
    """Same as at start-up: re-apply the DPMS policy (the X server may have lost it) and switch on the screens that
    should be on. The viewer needs nothing more: a screen that comes back is a RandR hot-plug and the viewer already
    follows those."""
    from api.ws.endpoints import WS
    from unitotem_node.system.screens import control, xorg

    async def broadcast():
        await WS.broadcast('Settings/Display/Screens/screens', screens=await control.screens(), probing=control.probing())

    try:
        try:
            await xorg.dpms_policy()
        except Exception as exc:
            logger.warning('Cannot apply the DPMS policy after resume: {}', exc)
        await control.wake_all(on_done=broadcast)
    except Exception:
        logger.exception('Waking the screens after resume failed')


async def watch_sleep(bus=None, wake_screens=wake_screens_after_resume, retry: float = 30):
    """Subscribes to logind's PrepareForSleep and handles it until cancelled. A lost connection (D-Bus restarted) is
    reopened after `retry` seconds: that is reconnecting, not polling."""
    while True:
        try:
            own = bus if bus is not None else sdbus.sd_bus_open_system()
            try:
                logind = dbus_system.get_logind(own)
                async for starting in logind.prepare_for_sleep:
                    try:
                        await on_prepare_for_sleep(starting, wake_screens)
                    except Exception:
                        logger.exception('Handling PrepareForSleep({}) failed', starting)
            finally:
                if bus is None:
                    own.close()
            logger.warning('The PrepareForSleep subscription ended')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Cannot follow logind sleep events ({}), retrying in {} s', exc, retry)
        await asyncio.sleep(retry)
