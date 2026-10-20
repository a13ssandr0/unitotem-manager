"""
The facade of screen control: what can be done to each output, and doing it.

Mechanisms: the compositor's own outputs (always there with a display), DDC/CI, CEC, Wake-on-LAN and serial. The first three are probed;
WOL and serial exist only when configured for the output. See SCREEN-CONTROL-PLAN.md for the capability model.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from unitotem_node.models.screen_control import OutputConfig, ScreenControl, screen_control
from . import cec, ddc_ctl, serial, wayland, wol
from ._exec import ToolMissing, ToolTimeout
from .ddc import DdcError
from .identity import same_output
from .mechanism import NoMechanismAvailable, candidates

_power_locks: dict[str, asyncio.Lock] = {}
_probing: Optional[asyncio.Task] = None


class ScreenControlError(Exception):
    """Something the operator can act on: a message for the web UI."""


def _lock(name: str) -> asyncio.Lock:
    return _power_locks.setdefault(name, asyncio.Lock())


async def _tools() -> dict:
    """What is installed and usable right now."""
    ddc_ok, ddc_reason = await ddc_ctl.available()
    return {'ddc': (ddc_ok, ddc_reason), 'cec_devices': await cec.devices()}


def _availability(name: str, cfg: OutputConfig, x_output: Optional[wayland.Output], tools: dict) -> dict[str, dict]:
    """{mechanism: {'available': bool, 'reason': str}} for one output."""
    ddc_ok, ddc_reason = tools['ddc']
    adapters = tools['cec_devices']
    cec_device = cfg.cec_device or (adapters[0].path if len(adapters) == 1 else None)
    result = {
        'output': {'available': x_output is not None,
                   'reason': '' if x_output is not None else 'the output is not connected to the compositor'},
        'ddc': {'available': ddc_ok and cfg.ddc_bus is not None and ddc_ctl.VCP_POWER in cfg.ddc_features,
                'reason': '' if ddc_ok and cfg.ddc_bus is not None and ddc_ctl.VCP_POWER in cfg.ddc_features else
                (ddc_reason or ('no DDC/CI found on this output (run a probe)' if cfg.ddc_bus is None
                                else 'this monitor has DDC/CI but no power control over it'))},
        'cec': {'available': cec_device is not None,
                'reason': '' if cec_device is not None else
                ('no CEC adapter' if not adapters else 'several CEC adapters: choose one for this output')},
        'wol': {'available': cfg.wol is not None, 'reason': '' if cfg.wol else 'no MAC address configured'},
        'serial': {'available': cfg.serial is not None,
                   'reason': '' if cfg.serial else 'no serial port configured'},
    }
    return result


async def screens() -> list[dict]:
    """Every output we know about: the ones X reports plus the configured ones that are not connected right now
    (a monitor in deep standby may disappear from X altogether)."""
    x_outputs = {o.name: o for o in await wayland.outputs()}
    tools = await _tools()
    names = list(x_outputs) + [n for n in screen_control.outputs if n not in x_outputs]
    out = []
    for name in names:
        cfg = screen_control.outputs.get(name) or OutputConfig()
        x = x_outputs.get(name)
        out.append({
            'name': name,
            'connected': x is not None,
            'active': bool(x and x.active),
            'geometry': {'x': x.x, 'y': x.y, 'width': x.width, 'height': x.height, 'rotation': x.rotation} if x else None,
            'identity': cfg.identity,
            'mechanisms': _availability(name, cfg, x, tools),
            'ddc_features': cfg.ddc_features,
            'input_sources': {k: v for k, v in cfg.ddc_capabilities.get(ddc_ctl.VCP_INPUT, {}).items()},
            'config': {
                'on_mechanism': cfg.on_mechanism, 'off_mechanism': cfg.off_mechanism,
                'power_on_at_start': cfg.power_on_at_start,
                'wol': cfg.wol.model_dump() if cfg.wol else None,
                'serial': cfg.serial.model_dump() if cfg.serial else None,
                'cec_device': cfg.cec_device,
            },
        })
    return out


async def _probe(only: Optional[str]):
    tools = await _tools()
    x_outputs = {o.name: o for o in await wayland.outputs()}
    identities = await wayland.identities()
    detected = []
    if tools['ddc'][0]:
        try:
            detected = await ddc_ctl.detect()
        except (ToolMissing, ToolTimeout) as exc:
            logger.warning('DDC detection failed: {}', exc)
    for name in x_outputs:
        if only and name != only:
            continue
        cfg = screen_control.output(name)
        cfg.identity = identities.get(name)
        match = next((d for d in detected if d.bus is not None and (
            (d.monitor and cfg.identity and d.monitor == cfg.identity) or
            (d.connector and same_output(name, d.connector)))), None)
        cfg.ddc_bus = match.bus if match else None
        if match:
            try:
                cfg.ddc_capabilities = await ddc_ctl.capabilities(match.bus)
            except (DdcError, ToolTimeout, ToolMissing) as exc:
                logger.warning('Cannot read the capabilities of {}: {}', name, exc)
            cfg.ddc_features = await ddc_ctl.probe_features(match.bus, cfg.ddc_capabilities)
        else:
            cfg.ddc_features = []
        logger.info('Probed {}: identity {} ddc bus {}', name, cfg.identity, cfg.ddc_bus)
    screen_control.save()


async def probe(only: Optional[str] = None, on_done=None):
    """Start the probe in the background (capabilities cost ~30 s per monitor) and return at once. `on_done` is
    awaited when it ends, whatever the outcome."""
    global _probing
    if _probing is not None and not _probing.done():
        return

    async def job():
        try:
            await _probe(only)
        except Exception:
            logger.exception('Screen probe failed')
        finally:
            if on_done is not None:
                await on_done()

    _probing = asyncio.get_running_loop().create_task(job(), name='screen_probe')


def probing() -> bool:
    return _probing is not None and not _probing.done()


async def _x_output(name: str) -> Optional[wayland.Output]:
    return next((o for o in await wayland.outputs() if o.name == name), None)


async def _do_power(name: str, mechanism: str, on: bool, cfg: OutputConfig, x: Optional[wayland.Output]):
    if mechanism == 'output':
        await wayland.set_power(name, on, known=x)
    elif mechanism == 'ddc':
        await ddc_ctl.set_power(cfg.ddc_bus, on, cfg.ddc_capabilities)
    elif mechanism == 'cec':
        adapters = await cec.devices()
        device = cfg.cec_device or adapters[0].path
        await cec.set_power(device, on)
    elif mechanism == 'wol':
        await wol.wake(cfg.wol.mac, cfg.wol.broadcast, cfg.wol.port, cfg.wol.interface, cfg.wol.repeat)
    elif mechanism == 'serial':
        command = cfg.serial.on_command if on else cfg.serial.off_command
        if not command:
            raise ScreenControlError(f'no serial command configured to switch {"on" if on else "off"}')
        await serial.send(cfg.serial.port, cfg.serial.baud, command)


async def set_power(name: str, state: str, mechanism: str = 'auto') -> str:
    """Switch an output on or off. Returns the mechanism that did it. On 'auto' the mechanisms are tried in order
    until one works; an explicit one is the only attempt."""
    if state not in ('on', 'off'):
        raise ScreenControlError("state must be 'on' or 'off'")
    on = state == 'on'
    async with _lock(name):
        cfg = screen_control.output(name)
        x = await _x_output(name)
        tools = await _tools()
        availability = _availability(name, cfg, x, tools)
        available = {m for m, v in availability.items() if v['available']}
        if on:
            # WOL and serial are about waking something the compositor cannot see; it can only switch on what it lists
            pass
        else:
            available.discard('wol')
        preference = mechanism if mechanism != 'auto' else (cfg.on_mechanism if on else cfg.off_mechanism)
        try:
            order = candidates(available, state, preference)  # type: ignore[arg-type]
        except NoMechanismAvailable as exc:
            raise ScreenControlError(str(exc)) from exc
        errors = []
        for mech in order:
            try:
                logger.info('Screen {}: switching {} with {}', name, state, mech)
                await _do_power(name, mech, on, cfg, x)
                return mech
            except (RuntimeError, DdcError, ToolMissing, ToolTimeout, OSError, ValueError, ScreenControlError) as exc:
                logger.warning('Screen {}: {} failed to switch {}: {}', name, mech, state, exc)
                errors.append(f'{mech}: {exc}')
        raise ScreenControlError('; '.join(errors))


async def power_state(name: str) -> dict:
    """Best-effort: what each mechanism that can tell says about the output. The compositor knows whether the output is driven; DDC and
    CEC are asked only when probed, and many monitors do not answer while off."""
    cfg = screen_control.output(name)
    x = await _x_output(name)
    state = {'output': ('on' if x.active else 'off') if x else None, 'ddc': None, 'cec': None}
    if cfg.ddc_bus is not None and (await ddc_ctl.available())[0]:
        state['ddc'] = await ddc_ctl.power_state(cfg.ddc_bus)
    adapters = await cec.devices()
    device = cfg.cec_device or (adapters[0].path if len(adapters) == 1 else None)
    if device:
        state['cec'] = await cec.power_state(device)
    return state


def _need_ddc(name: str) -> int:
    cfg = screen_control.output(name)
    if cfg.ddc_bus is None:
        raise ScreenControlError('DDC/CI is not available on this output (run a probe)')
    return cfg.ddc_bus


async def set_input(name: str, source: int):
    bus = _need_ddc(name)
    advertised = screen_control.output(name).ddc_capabilities.get(ddc_ctl.VCP_INPUT)
    if advertised and f'{source:02x}' not in advertised:
        # Offering a value the monitor never advertised is a guess; refuse it
        raise ScreenControlError(f'the monitor does not offer input {source:#04x}')
    try:
        await ddc_ctl.setvcp(bus, ddc_ctl.VCP_INPUT, source)
    except DdcError as exc:
        raise ScreenControlError(str(exc)) from exc


async def set_volume(name: str, percent: int):
    bus = _need_ddc(name)
    try:
        current = await ddc_ctl.getvcp(bus, ddc_ctl.VCP_VOLUME)
        if current is None or not current.max_value:
            raise ScreenControlError('this monitor has no volume control over DDC/CI')
        await ddc_ctl.setvcp(bus, ddc_ctl.VCP_VOLUME, round(current.max_value * max(0, min(100, percent)) / 100))
    except DdcError as exc:
        raise ScreenControlError(str(exc)) from exc


async def set_mute(name: str, mute: bool):
    bus = _need_ddc(name)
    try:
        await ddc_ctl.setvcp(bus, ddc_ctl.VCP_MUTE, ddc_ctl.MUTE_ON if mute else ddc_ctl.MUTE_OFF)
    except DdcError as exc:
        raise ScreenControlError(str(exc)) from exc


def configure(name: str, **changes):
    """Change the settings of one output (validated by the model) and save them."""
    cfg = screen_control.output(name)
    data = cfg.model_dump()
    for key, value in changes.items():
        if key in ('wol', 'serial') and value is not None and not isinstance(value, dict):
            raise ScreenControlError(f'{key} must be an object')
        data[key] = value
    try:
        screen_control.outputs[name] = OutputConfig(**data)
    except ValueError as exc:
        raise ScreenControlError(str(exc)) from exc
    screen_control.save()


async def wake_all(budget: float = 240, interval: float = 20, on_done=None):
    """After the manager starts: switch on every screen that is not driven, retrying for `budget` seconds. A monitor
    that stayed off after a boot or a power cut needs this, and some take the best part of a minute to show their
    source once woken (55 s measured on a touch panel woken by Wake-on-LAN), so a retry has to leave that much time
    before it counts as failed. Once it is on, the compositor reports it and the viewer opens its window by itself (hot-plug)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    attempt = 0
    while True:
        attempt += 1
        x_outputs = {o.name: o for o in await wayland.outputs()}
        todo = [name for name, cfg in screen_control.outputs.items()
                if cfg.power_on_at_start and not (name in x_outputs and x_outputs[name].active)]
        todo += [name for name, o in x_outputs.items() if not o.active and name not in todo
                 and screen_control.outputs.get(name, OutputConfig()).power_on_at_start]
        if not todo:
            break
        for name in todo:
            try:
                mech = await set_power(name, 'on')
                logger.info('Woke screen {} with {} (attempt {})', name, mech, attempt)
            except ScreenControlError as exc:
                logger.warning('Cannot wake screen {} (attempt {}): {}', name, attempt, exc)
        if loop.time() + interval > deadline:
            break
        await asyncio.sleep(interval)
    if on_done is not None:
        await on_done()
