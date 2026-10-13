"""
Settings of this node's own viewer, persisted across restarts.

Kept apart from the playlists and from the viewer -> playlist assignments:
those describe *what* is shown and *where*, this one describes how the local
browser itself behaves, and it applies to every window of this node.
"""
import json
from pathlib import Path

from loguru import logger
from typing import Literal

from pydantic import BaseModel, Field

from utils.models.command_line import cmdargs


class KeyboardSettings(BaseModel, validate_assignment=True):
    """The on-screen keyboard of one window (a touch panel): off unless asked for."""
    enabled: bool = False
    # where the button that opens it is, on the screen
    button_position: Literal['top-left', 'top', 'top-right', 'left', 'right',
                             'bottom-left', 'bottom', 'bottom-right'] = 'bottom-right'
    # a layout of simple-keyboard-layouts ('english', 'italian', 'german'...)
    layout: str = Field(default='english', pattern=r'^[A-Za-z]{2,32}$')


class ViewerSettings(BaseModel, validate_assignment=True):
    # Whether the viewer displays assets served over https with an invalid
    # certificate (self-signed, expired, wrong hostname). Off by default:
    # with it on, anything able to intercept the connection can decide what
    # ends up on the screen. Read by unitotem_viewer.app.WebviewApp at CEF startup,
    # so a change only takes effect on the next viewer start.
    allow_insecure_certs: bool = False

    # Where each window sends its sound: "<viewer id>|<screen name>" -> output description, absent for the default.
    # Keyed on the screen like the playlist assignments, so it survives unplugging and replugging a monitor.
    audio_outputs: dict[str, str] = {}

    # The on-screen keyboard of each window, keyed like the audio outputs
    keyboards: dict[str, KeyboardSettings] = {}

    @staticmethod
    def _filepath() -> Path:
        """Beside the playlists, i.e. in the configuration directory."""
        return Path(cmdargs.assets_file).parent / 'viewer_settings.json'

    @classmethod
    def load(cls) -> 'ViewerSettings':
        """Read the stored settings, falling back to the defaults for a node
        that has never had any saved (the normal case on first boot)."""
        path = cls._filepath()
        try:
            with open(path) as file:
                return cls(**json.load(file))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.error('Cannot read viewer settings from {} ({}), using defaults', path, exc)
            return cls()

    def save(self):
        path = self._filepath()
        try:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w') as file:
                json.dump(self.model_dump(), file, indent=4)
            tmp.replace(path)   # atomic: a power cut cannot leave a half file
        except OSError as exc:
            logger.error('Cannot save viewer settings to {}: {}', path, exc)


viewer_settings = ViewerSettings.load()
