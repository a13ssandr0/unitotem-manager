"""Running the external tools of the screen layer without ever blocking the event loop."""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass


class ToolMissing(Exception):
    """The program is not installed. A mechanism built on it is simply unavailable, not broken."""


class ToolTimeout(Exception):
    pass


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


def _run(cmd: list[str], timeout: float) -> Result:
    # The environment is inherited on purpose: the manager's unit sets WAYLAND_DISPLAY and XDG_RUNTIME_DIR for wlr-randr/wlopm
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors='replace')
    except FileNotFoundError as exc:
        raise ToolMissing(cmd[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeout(f'{cmd[0]} did not answer within {timeout:.0f} s') from exc
    return Result(proc.returncode, proc.stdout, proc.stderr)


async def run(cmd: list[str], timeout: float = 10) -> Result:
    return await asyncio.to_thread(_run, cmd, timeout)
