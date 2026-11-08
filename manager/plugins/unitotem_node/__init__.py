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
    from .system.screens import control, wayland
    from .system.timer_actions import run_timers

    async def screens_at_start():
        """Wake the screens that are off (a monitor that stayed off after a boot or a power cut), then probe what each
        one supports. Neither needs a display to exist: on a headless node both simply find nothing to do."""
        async def broadcast():
            await WS.broadcast('Settings/Display/Screens/screens', screens=await control.screens(),
                               probing=control.probing())

        try:
            try:
                await wayland.wake_all()
            except Exception as exc:
                logger.warning('Cannot wake the outputs: {}', exc)
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

    async def update_progress_at_start():
        """Tell the pages (and the bell) what the updater is doing, also when the manager did not start it."""
        from .system.update_progress import update_progress

        async def pushed(status):
            await WS.broadcast('Settings/Update/status', status=status)

        try:
            await update_progress.start(pushed)
        except Exception:
            logger.exception('Update progress watch failed to start')

    async def touch_at_start():
        """Map every touch screen to its output now, and again whenever one (or a screen) appears or goes away."""
        from .system.touch_service import touch_service

        from .system.touch_setup import touch_setup

        async def broadcast():
            await WS.broadcast('Settings/Display/Touch/devices', **touch_service.snapshot())
            if touch_setup.session is not None:                     # a setup of a panel that was unplugged cannot go on
                await touch_setup.panel_gone(touch_setup.session.key)

        async def setup_changed(state: dict):
            await WS.broadcast('Settings/Display/Touch/setup', **state)

        touch_setup.bind(setup_changed)

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
    loop.create_task(update_progress_at_start(), name='update_progress')
    from .system import dbus_system

    async def hostname_watch():
        async def pushed(name):
            await WS.broadcast('Settings/Network/hostname', hostname=name)

        try:
            await dbus_system.watch_hostname(pushed)
        except Exception:
            logger.exception('Hostname watch stopped')

    loop.create_task(hostname_watch(), name='hostname_watch')

    async def datetime_watch():
        from .api.date_time import status_fields

        async def pushed():
            await WS.broadcast('Settings/DateTime/status', **await status_fields())

        try:
            await dbus_system.watch_timedate(pushed)
        except Exception:
            logger.exception('Date and time watch stopped')

    loop.create_task(datetime_watch(), name='datetime_watch')
    from .system import desktop_notifications
    loop.create_task(desktop_notifications.serve(), name='desktop_notifications')


# ── first boot: the hotspot that lets someone in ─────────────────────────────

async def on_first_boot():
    from utils.system.ip import default_ipv4
    from .system.network.hotspot import start_hotspot
    try:
        if not default_ipv4():
            ssid, passwd = await start_hotspot()
            if ssid:
                logger.info(f'Not connected to any network, started fallback hotspot {ssid} with password {passwd}.')
            else:
                logger.error("Couldn't start wifi hotspot: start_hotspot returned no network")
    except Exception as e:
        logger.error(f"Couldn't start wifi hotspot: {e}")


async def first_boot_context() -> dict:
    from .system.network.hotspot import get_hotspot_with_qr, is_hotspot_enabled
    return {'wifi': await get_hotspot_with_qr() if await is_hotspot_enabled() else None}


async def on_shutdown():
    from .system.screens import gpio
    gpio.release_all()
    from . import vnc
    await vnc.stop_all()
    from .system.touch_service import touch_service
    touch_service.stop()
    from .system.update_progress import update_progress
    update_progress.stop()
    from .system.network.hotspot import stop_hotspot
    await stop_hotspot()


def audio_outputs() -> list[dict]:
    from pulsectl import PulseError
    from .system.audio import get_audio_devices
    try:
        return [{'name': d['name'], 'description': d['description']} for d in get_audio_devices()]
    except PulseError:
        return []


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


def _checked(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f'{what} is not a list of entries')
    return value


def _export_touch():
    from .models.touch_map import touch_map
    return {'mapping': dict(touch_map.mapping), 'orientation': dict(touch_map.orientation),
            'calibration': {k: list(m) for k, m in touch_map.calibration.items()}}


def _restore_touch(data, _version):
    """Every entry is validated by the model's own setters before anything is stored. An archive from before the
    orientation and the calibration were backed up is the flat mapping of device to output."""
    from .models.touch_map import TouchMap, touch_map
    data = _checked(data, 'The touch mapping')
    if not isinstance(data.get('mapping'), dict):
        data = {'mapping': data}
    fresh = TouchMap()
    for key, output in _checked(data.get('mapping', {}), 'The mapping').items():
        if not isinstance(output, str):
            raise ValueError(f'{key}: the output is not a name')
        fresh.set(key, output)
    for key, turn_flip in _checked(data.get('orientation', {}), 'The orientation').items():
        if not isinstance(turn_flip, dict):
            raise ValueError(f'{key}: the orientation is not turn and flip')
        fresh.set_orientation(key, turn_flip.get('turn'), turn_flip.get('flip'))
    for key, matrix in _checked(data.get('calibration', {}), 'The calibration').items():
        fresh.set_calibration(key, matrix)          # a calibration replaces the orientation of its panel
    touch_map.mapping, touch_map.orientation, touch_map.calibration = fresh.mapping, fresh.orientation, fresh.calibration
    touch_map.save()
    _touch_changed()


def _reset_touch():
    from .models.touch_map import touch_map
    touch_map.mapping, touch_map.orientation, touch_map.calibration = {}, {}, {}
    touch_map.save()
    _touch_changed()


def _touch_changed():
    """The touch input follows the stored mapping: applied now when the loop runs, else at the next start."""
    from .system.touch_service import touch_service
    try:
        asyncio.get_running_loop().create_task(touch_service.refresh())
    except RuntimeError:
        pass        # no loop: the next start applies the stored mapping


# The configuration of each output (mechanism, Wake-on-LAN, serial, GPIO, CEC adapter). The probe results (DDC bus,
# advertised inputs, identity) describe the machine the archive was taken on: they are never restored, the local ones stay.
_SCREEN_PROBE_FIELDS = ('identity', 'ddc_bus', 'ddc_capabilities', 'ddc_features')


def _screen_config(name: str, entry: dict, current):
    """A validated output with the configuration of `entry` and the probe results this machine already has."""
    from .models.screen_control import OutputConfig
    if not isinstance(entry, dict):
        raise ValueError(f'{name}: not an output configuration')
    config = {k: v for k, v in entry.items() if k not in _SCREEN_PROBE_FIELDS}
    probe = {f: getattr(current, f) for f in _SCREEN_PROBE_FIELDS} if current is not None else {}
    return OutputConfig(**config, **probe)          # the model's validators refuse a bad value


def _export_screens():
    from .models.screen_control import screen_control
    return {name: cfg.model_dump(exclude=set(_SCREEN_PROBE_FIELDS)) for name, cfg in screen_control.outputs.items()}


def _restore_screens(outputs, _version):
    from .models.screen_control import OUTPUT_NAME_RE, ScreenControl, screen_control
    outputs = _checked(outputs, 'The screen outputs')
    restored = {}
    for name, entry in outputs.items():
        if not OUTPUT_NAME_RE.fullmatch(name):
            raise ValueError(f'{name!r} is not an output name')
        restored[name] = _screen_config(name, entry, screen_control.outputs.get(name))
    screen_control.outputs = ScreenControl(outputs=restored).outputs
    screen_control.save()


def _reset_screens():
    from .models.screen_control import screen_control
    screen_control.outputs = {name: _screen_config(name, {}, cfg) for name, cfg in screen_control.outputs.items()}
    screen_control.save()


BACKUP = (
    BackupPart('hostname', 'Hostname', lambda: socket.gethostname(), _restore_hostname),
    BackupPart('audio', 'Default audio device', _export_audio, _restore_audio),
    BackupPart('timers', 'Timers', _export_timers, _restore_timers, _reset_timers),
    BackupPart('touch', 'Touch screens (mapping, orientation, calibration)', _export_touch, _restore_touch, _reset_touch),
    BackupPart('screens', 'Screen power and control', _export_screens, _restore_screens, _reset_screens),
)


def _routers():
    from .terminal import router
    from .vnc import router as vnc_router
    from .devtools import router as devtools_router
    return (router, vnc_router, devtools_router)


PLUGIN = Plugin(
    name='node',
    api=(
        ('unitotem_node.api.audio', 'Audio', 'Settings'),
        ('unitotem_node.api.network', 'Network', 'Settings'),
        ('unitotem_node.api.timers', 'Timers', 'Settings'),
        ('unitotem_node.api.date_time', 'DateTime', 'Settings'),
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
)
