"""
The machine itself: network, audio, displays and screen power, power buttons, timers, updates, the terminal and the
hotspot of the first boot. A server that only orchestrates other nodes does not install this.
"""
import asyncio
import socket

from loguru import logger
from packaging.version import Version

from utils.plugins import BackupPart, Plugin

UPDATE_CHECK_FIRST_DELAY = 180      # the network may not be up yet right after a boot
UPDATE_CHECK_INTERVAL = 24 * 3600


# ── start-up ─────────────────────────────────────────────────────────────────

def on_startup(loop):
    from api.commons import SHUTDOWN_EVENT
    from api.ws.endpoints import WS
    from .system import suspend, updater
    from .system.screens import control, xorg
    from .system.timer_actions import run_timers

    async def screens_at_start():
        """Wake the screens that are off (a monitor that stayed off after a boot or a power cut), then probe what each
        one supports. Neither needs a display to exist: on a headless node both simply find nothing to do."""
        async def broadcast():
            await WS.broadcast('Settings/Display/Screens/screens', screens=await control.screens(),
                               probing=control.probing())

        try:
            try:
                await xorg.dpms_policy()
            except Exception as exc:
                logger.warning('Cannot apply the DPMS policy: {}', exc)
            await control.wake_all(on_done=broadcast)
            await control.probe(on_done=broadcast)
        except Exception:
            logger.exception('Screen start-up failed')

    async def update_watch():
        """Check for updates in the background once a day and tell every open page when the answer changes."""
        await asyncio.sleep(UPDATE_CHECK_FIRST_DELAY)
        while not SHUTDOWN_EVENT.is_set():
            try:
                if updater.record(await updater.check()):
                    await WS.broadcast('Settings/Update/availability', availability=updater.availability)
            except Exception:
                logger.exception('Daily update check failed')
            try:
                await asyncio.wait_for(SHUTDOWN_EVENT.wait(), UPDATE_CHECK_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def touch_at_start():
        """Map every touch screen to its output now, and again whenever one (or a screen) appears or goes away."""
        from .system.touch_service import touch_service

        async def broadcast():
            await WS.broadcast('Settings/Display/Touch/devices', **touch_service.snapshot())

        try:
            await touch_service.start(broadcast)
        except Exception:
            logger.exception('Touch mapping start-up failed')

    from .system import vnc
    vnc.sweep_stale()       # a screen capture left behind by a manager that was killed
    loop.create_task(screens_at_start(), name='screens_at_start')
    loop.create_task(touch_at_start(), name='touch_at_start')
    loop.create_task(run_timers(), name='timers')
    loop.create_task(suspend.watch_sleep(), name='sleep_watch')
    loop.create_task(update_watch(), name='update_watch')


# ── first boot: the hotspot that lets someone in ─────────────────────────────

async def on_first_boot():
    from utils.system.ip import default_ipv4
    from .system.network.hotspot import start_hotspot
    try:
        if not default_ipv4():
            ssid, passwd = await start_hotspot()
            logger.info(f'Not connected to any network, started fallback hotspot {ssid} with password {passwd}.')
    except Exception as e:
        logger.error(f"Couldn't start wifi hotspot: {e}")


async def first_boot_context() -> dict:
    from .system.network.hotspot import get_hotspot_with_qr, is_hotspot_enabled
    return {'wifi': await get_hotspot_with_qr() if await is_hotspot_enabled() else None}


async def on_shutdown():
    from . import vnc
    await vnc.stop_all()
    from .system.touch_service import touch_service
    touch_service.stop()
    from .system.network.hotspot import stop_hotspot
    await stop_hotspot()


def audio_outputs() -> list[dict]:
    from pulsectl import PulseError
    from .system.audio import get_audio_devices
    try:
        return [{'name': d['name'], 'description': d['description']} for d in get_audio_devices()]
    except PulseError:
        return []


def os_account():
    from .system.os_account import account
    return account()


async def set_os_password(password: str) -> bool:
    from .system.os_account import set_password
    return await set_password(password)


# ── backup ───────────────────────────────────────────────────────────────────

async def _restore_hostname(name, _version):
    from .system.dbus_system import set_static_hostname
    await set_static_hostname(name)


def _restore_audio(device, version):
    from .system.audio import set_default_audio_device
    # with version 3.0.0 audio controls changed from alsa to pulseaudio: an older name means nothing here
    if Version(version) >= Version('3.0.0'):
        set_default_audio_device(device)


def _export_audio():
    from .system.audio import get_default_audio_device
    try:
        return get_default_audio_device()
    except Exception:
        return None         # no sound server on this machine


def _export_netplan():
    from .system.network.netplan import get_netplan_file, get_netplan_file_list
    return {name: get_netplan_file(name) for name in get_netplan_file_list()}


def _restore_netplan(files, _version):
    from .system.network.netplan import generate_netplan, set_netplan
    set_netplan(filename=None, file_content=files, apply=False)
    result = generate_netplan()
    if isinstance(result, str):
        raise ValueError(result)


def _export_timers():
    from .models.timers import timer_manager
    return [t.model_dump() for t in timer_manager.timers.values()]


def _restore_timers(items, _version):
    from .models.timers import Timer, timer_manager
    timer_manager.timers = {t.uuid: t for t in (Timer(**item) for item in items)}
    timer_manager.save()


def _reset_timers():
    from .models.timers import timer_manager
    timer_manager.timers = {}
    timer_manager.save()


def _export_touch():
    from .models.touch_map import touch_map
    return dict(touch_map.mapping)


def _restore_touch(mapping, _version):
    from .models.touch_map import TouchMap, touch_map
    touch_map.mapping = TouchMap(mapping=mapping).mapping       # validated like any other write
    touch_map.save()


def _reset_touch():
    from .models.touch_map import touch_map
    touch_map.mapping = {}
    touch_map.save()


BACKUP = (
    BackupPart('hostname', 'Hostname', lambda: socket.gethostname(), _restore_hostname),
    BackupPart('audio', 'Default audio device', _export_audio, _restore_audio),
    BackupPart('netplan', 'Network (netplan)', _export_netplan, _restore_netplan),
    BackupPart('timers', 'Timers', _export_timers, _restore_timers, _reset_timers),
    BackupPart('touch', 'Touch screen mapping', _export_touch, _restore_touch, _reset_touch),
)


def _routers():
    from .terminal import router
    from .vnc import router as vnc_router
    return (router, vnc_router)


PLUGIN = Plugin(
    name='node',
    api=(
        ('unitotem_node.api.audio', 'Audio', 'Settings'),
        ('unitotem_node.api.network', 'Network', 'Settings'),
        ('unitotem_node.api.timers', 'Timers', 'Settings'),
        ('unitotem_node.api.power', 'Power', None),
        ('unitotem_node.api.updater', 'Update', 'Settings'),
        ('unitotem_node.api.display', 'Display', 'Settings'),
        ('unitotem_node.api.vnc', 'Vnc', None),
    ),
    routers=_routers,
    backup=BACKUP,
    on_startup=on_startup,
    on_first_boot=on_first_boot,
    first_boot_context=first_boot_context,
    on_shutdown=on_shutdown,
    audio_outputs=audio_outputs,
    os_account=os_account,
    set_os_password=set_os_password,
)
