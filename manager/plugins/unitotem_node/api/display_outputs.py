"""
Resolution and refresh rate of this node's own outputs, with a timed confirmation.

Paths: Settings/Display/Outputs/<function> (bound into unitotem_node.api.display.Display). A new mode is applied at once and put back
after REVERT_AFTER seconds unless confirm_mode arrives, so a picture nobody can see is restored by itself. Only a confirmed mode is
saved (viewer_settings.modes, key `local-webview|<output>`) and replayed at boot by unitotem_viewer/output_transform.py. The timer is
in memory: if the manager restarts while a change waits, the compositor keeps the new mode and nothing is saved.

Every change answers on `outputs`, the list of describe() entries.
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from api.ws.responses import WSBroadcast, WSResponse
from api.ws.wsmanager import WSAPIBase
from unitotem_node.system.screens import wayland
from utils.models.viewer import OutputMode, viewer_settings
from webview.controller import WebviewManager

# Same key as the local viewer everywhere else (unitotem_viewer/output_transform.py, utils/remote/bundle.py)
LOCAL_VIEWER = 'local-webview'

REVERT_AFTER = 15.0     # seconds a new mode stays unless confirmed (a module attribute, so a test can shorten it)


@dataclass
class _Pending:
    previous: tuple          # (width, height, refresh_mhz) to put back: the mode before the first unconfirmed change
    applied: tuple
    revert_at: float         # time.time() of the revert
    task: asyncio.Task


_pending: dict[str, _Pending] = {}


def _key(name: str) -> str:
    return f'{LOCAL_VIEWER}|{name}'


def _mode(width: int, height: int, refresh_mhz: int) -> dict:
    return {'width': width, 'height': height, 'refresh_mhz': refresh_mhz}


def _cancel(name: str) -> Optional[_Pending]:
    """Forget the change waiting on an output and stop its timer, without reverting it."""
    pending = _pending.pop(name, None)
    if pending is not None:
        pending.task.cancel()
    return pending


async def _reset_windows(name: str):
    """The turn and mirror the windows on an output show go back to none, as the web UI's own setters do."""
    try:
        manager = WebviewManager.get_instance()
    except RuntimeError:          # no viewer manager (a headless node)
        return
    for window in manager.get_webview_windows(LOCAL_VIEWER):
        if window.get('screen_name') == name:
            await manager.set_orientation(LOCAL_VIEWER, window.get('window_id', 0), 0)
            await manager.set_flip(LOCAL_VIEWER, window.get('window_id', 0), 0)


async def describe() -> list[dict]:
    """Every connected output: its modes, its current mode, the saved mode, turn and mirror, and the change waiting for confirmation."""
    found = []
    for output in await wayland.outputs():
        key = _key(output.name)
        current = output.current_mode
        saved = viewer_settings.modes.get(key)
        pending = _pending.get(output.name)
        found.append({
            'name': output.name,
            'enabled': output.enabled,
            'transform': output.transform,
            'current': _mode(current.width, current.height, current.refresh_mhz) if current else None,
            'modes': [dict(_mode(m.width, m.height, m.refresh_mhz), preferred=m.preferred, current=m.current) for m in output.modes],
            'saved': _mode(saved.width, saved.height, saved.refresh_mhz) if saved else None,
            'orientation': viewer_settings.orientations.get(key, 0),
            'flip': viewer_settings.flips.get(key, 0),
            'pending': dict(_mode(*pending.applied), revert_at=pending.revert_at) if pending else None,
        })
    return found


class Outputs(WSAPIBase):
    async def _broadcast(self):
        await self.ws.broadcast('Settings/Display/Outputs/outputs', outputs=await describe())

    async def outputs(self):
        """Every connected output with its modes, current mode, saved mode, turn, mirror and pending change."""
        return WSBroadcast(self.outputs, outputs=await describe())

    async def set_mode(self, name: str, width: int, height: int, refresh_mhz: int):
        """Apply a mode the output lists; it is reverted after REVERT_AFTER seconds unless confirm_mode arrives."""
        try:
            wayland.check_output_name(name)
            previous = _pending[name].previous if name in _pending else None
            if previous is None:
                current = next((o for o in await wayland.outputs() if o.name == name), None)
                if current is None or current.current_mode is None:
                    raise ValueError(f'{name} has no current mode to return to')
                previous = (current.current_mode.width, current.current_mode.height, current.current_mode.refresh_mhz)
            await wayland.set_mode(name, width, height, refresh_mhz)
        except (ValueError, RuntimeError) as exc:
            return WSResponse(error=str(exc))
        _cancel(name)
        pending = _Pending(previous=previous, applied=(width, height, refresh_mhz), revert_at=time.time() + REVERT_AFTER,
                           task=None)
        pending.task = asyncio.create_task(self._revert_later(name, pending))
        _pending[name] = pending
        return WSBroadcast(self.outputs, outputs=await describe())

    async def _revert_later(self, name: str, pending: _Pending):
        await asyncio.sleep(REVERT_AFTER)
        if _pending.get(name) is not pending:          # confirmed, reset or superseded meanwhile
            return
        del _pending[name]
        try:
            await wayland.set_mode(name, *pending.previous)
        except (ValueError, RuntimeError) as exc:
            logger.warning('Cannot restore the mode of {}: {}', name, exc)
        await self._broadcast()

    async def confirm_mode(self, name: str):
        """Keep the mode waiting on an output: it is saved now and stays after the restart."""
        pending = _cancel(name)
        if pending is None:
            return WSResponse(error=f'No mode change waiting for confirmation on {name}')
        width, height, refresh_mhz = pending.applied
        viewer_settings.modes[_key(name)] = OutputMode(width=width, height=height, refresh_mhz=refresh_mhz)
        viewer_settings.save()
        return WSBroadcast(self.outputs, outputs=await describe())

    async def reset_output(self, name: str):
        """Preferred mode, normal transform, and nothing saved for the output (mode, turn, mirror)."""
        try:
            await wayland.reset_mode(name)
        except (ValueError, RuntimeError) as exc:
            return WSResponse(error=str(exc))
        _cancel(name)
        await _reset_windows(name)
        key = _key(name)
        viewer_settings.modes.pop(key, None)
        viewer_settings.orientations.pop(key, None)
        viewer_settings.flips.pop(key, None)
        viewer_settings.save()
        return WSBroadcast(self.outputs, outputs=await describe())
