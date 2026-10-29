"""
Turns and mirrors the outputs the way the settings say, the moment the compositor answers: run by unitotem-compositor.service
(ExecStartPost), before the boot splash draws, so that every picture from the first one is already turned.

A plain script with no imports from the rest of UniTotem (it must start in a fraction of a second and never fail): the stored
values are those of viewer_settings.json (`orientations`, `flips`, keyed `<viewer id>|<output name>`), the same ones the web UI
sets, and CONTENT_TO_TRANSFORM is the table of unitotem_node.system.screens.wayland (a test keeps the two equal).

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


def plan(settings: dict, outputs: set) -> list:
    """[(output, transform)] for the outputs of this node that have a turn or a mirror saved and are present now."""
    turns, flips = settings.get('orientations') or {}, settings.get('flips') or {}
    wanted = []
    for key in sorted(set(turns) | set(flips)):
        viewer, _, name = key.partition('|')
        if viewer != LOCAL_VIEWER or name not in outputs:
            continue
        turn, flip = turns.get(key, 0), flips.get(key, 0)
        if isinstance(turn, bool) or not isinstance(turn, int) or isinstance(flip, bool) or flip not in (0, 1, 2):
            continue
        transform = CONTENT_TO_TRANSFORM[(turn % 4, flip)]
        if transform != 'normal':
            wanted.append((name, transform))
    return wanted


def main(config_dir: str) -> int:
    env = dict(os.environ, XDG_RUNTIME_DIR=os.environ.get('XDG_RUNTIME_DIR', '/run/unitotem/xdg'), WAYLAND_DISPLAY='wayland-0')
    try:
        with open(os.path.join(config_dir, 'viewer_settings.json')) as file:
            settings = json.load(file)
        listed = subprocess.run(['wlr-randr', '--json'], env=env, capture_output=True, text=True, timeout=5)
        outputs = {entry['name'] for entry in json.loads(listed.stdout)}
        wanted = plan(settings, outputs)
        if wanted:
            command = ['wlr-randr']
            for name, transform in wanted:
                command += ['--output', name, '--transform', transform]
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
