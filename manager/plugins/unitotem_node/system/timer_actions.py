"""What each timer action does, and the loop that fires them at the start of every matching minute."""
import asyncio
from datetime import datetime

from loguru import logger

from api.commons import SHUTDOWN_EVENT
from unitotem_node.models.timers import Timer, timer_manager


async def execute(timer: Timer):
    from unitotem_node.system.dbus_system import system_poweroff, system_reboot
    from unitotem_node.system.screens import control
    if timer.action == 'pwr':
        await system_poweroff()
    elif timer.action == 'reb':
        await system_reboot()
    else:
        mechanism = await control.set_power(timer.output, timer.action.removeprefix('scr_'), timer.mechanism)
        logger.info('Timer {}: screen {} switched {} with {}', timer.uuid, timer.output,
                    timer.action.removeprefix('scr_'), mechanism)


async def run_timers():
    """Sleeps to the next minute boundary (a wait on the clock, not a poll) and runs what is due. A minute missed
    because the manager was down or the clock jumped is not made up, like cron."""
    while not SHUTDOWN_EVENT.is_set():
        now = datetime.now()
        await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.05)
        when = datetime.now().replace(second=0, microsecond=0)
        for timer in timer_manager.due(when):
            logger.info('Running timer {} ({})', timer.uuid, timer.action)
            try:
                await execute(timer)
            except Exception:
                logger.exception('Timer {} failed', timer.uuid)
