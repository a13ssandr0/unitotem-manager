"""
Sets the mode and turns and mirrors the outputs the way the settings say, the moment the compositor answers: run by unitotem-compositor.service
(ExecStartPost), before the boot splash draws, so that every picture from the first one is already turned.

A plain script with no imports from the rest of UniTotem (it must start in a fraction of a second and never fail): the stored
values are those of viewer_settings.json (`modes`, `orientations`, `flips`, keyed `<viewer id>|<output name>`), the same ones the web UI
sets. CONTENT_TO_TRANSFORM is the table of unitotem_node.system.screens.wayland and mode_argument its `--mode` format (a test keeps
the two equal). A saved mode is applied only when the compositor still lists it.

    output_transform.py [CONFIG_DIR]
"""
import json
import os
import subprocess
import sys

LOCAL_VIEWER = 'local-webview'
PENDING = '/run/unitotem/xdg/output-pending'     # made by the unit before the compositor starts, removed here when done

CONTENT_TO_TRANSFORM = {
    (0, 0): 'normal', (0, 1): 'flipped', (0, 2): 'flipped-180',
    (1, 0): '270', (1, 1): 'flipped-270', (1, 2): 'flipped-90',
    (2, 0): '180', (2, 1): 'flipped-180', (2, 2): 'flipped',
    (3, 0): '90', (3, 1): 'flipped-90', (3, 2): 'flipped-270',
}


def mode_argument(width: int, height: int, refresh_mhz: int) -> str:
    """wlr-randr's `--mode` value (WIDTHxHEIGHT@RATEHz), the rate in exact thousandths of a hertz."""
    return f'{width}x{height}@{refresh_mhz // 1000}.{refresh_mhz % 1000:03d}Hz'


def listed_modes(entries) -> dict:
    """Output name -> the set of (width, height, refresh_mhz) that `wlr-randr --json` lists for it. Malformed entries are skipped."""
    found = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
            continue
        modes = found.setdefault(entry['name'], set())
        for raw in entry.get('modes') or []:
            try:
                width, height = int(raw['width']), int(raw['height'])
                refresh_mhz = round(float(raw['refresh']) * 1000)
            except (KeyError, TypeError, ValueError):
                continue
            if width > 0 and height > 0 and refresh_mhz > 0:
                modes.add((width, height, refresh_mhz))
    return found


def saved_mode(value, listed: set):
    """The saved (width, height, refresh_mhz) when the compositor lists it, else None."""
    if not isinstance(value, dict):
        return None
    try:
        mode = (int(value['width']), int(value['height']), int(value['refresh_mhz']))
    except (KeyError, TypeError, ValueError):
        return None
    return mode if mode in listed else None


def saved_transform(turn, flip):
    """The wlr-randr transform of a saved turn and flip, None when there is none (normal) or the values cannot be read."""
    if isinstance(turn, bool) or not isinstance(turn, int) or isinstance(flip, bool) or flip not in (0, 1, 2):
        return None
    transform = CONTENT_TO_TRANSFORM[(turn % 4, flip)]
    return None if transform == 'normal' else transform


def plan(settings: dict, outputs: dict) -> list:
    """[(output, wlr-randr options)] for the outputs of this node that have a mode, a turn or a mirror saved and are present now.
    `outputs` maps each output name to the modes the compositor lists for it (see listed_modes)."""
    turns, flips = settings.get('orientations') or {}, settings.get('flips') or {}
    modes = settings.get('modes') or {}
    wanted = []
    for key in sorted(set(turns) | set(flips) | set(modes)):
        viewer, _, name = key.partition('|')
        if viewer != LOCAL_VIEWER or name not in outputs:
            continue
        args = []
        mode = saved_mode(modes.get(key), outputs[name])
        if mode is not None:
            args += ['--mode', mode_argument(*mode)]
        transform = saved_transform(turns.get(key, 0), flips.get(key, 0))
        if transform is not None:
            args += ['--transform', transform]
        if args:
            wanted.append((name, ['--output', name] + args))
    return wanted


def main(config_dir: str) -> int:
    env = dict(os.environ, XDG_RUNTIME_DIR=os.environ.get('XDG_RUNTIME_DIR', '/run/unitotem/xdg'), WAYLAND_DISPLAY='wayland-0')
    try:
        with open(os.path.join(config_dir, 'viewer_settings.json')) as file:
            settings = json.load(file)
        listed = subprocess.run(['wlr-randr', '--json'], env=env, capture_output=True, text=True, timeout=5)
        wanted = plan(settings, listed_modes(json.loads(listed.stdout)))
        if wanted:
            # one invocation: the mode and the transform of every output are one configuration
            command = ['wlr-randr']
            for _, options in wanted:
                command += options
            subprocess.run(command, env=env, capture_output=True, timeout=5)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'unitotem output transform: {error}', file=sys.stderr)
    finally:
        try:
            os.unlink(PENDING)
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else '/etc/unitotem'))
