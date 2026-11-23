"""--version is answered by main.py before any configuration is read (a subprocess: nothing of the manager starts)."""
import os
import subprocess
import sys
from pathlib import Path

MANAGER = Path(__file__).resolve().parents[1]


def test_version_prints_the_version_and_exits_zero(tmp_path):
    release_file = tmp_path / 'os-release'
    release_file.write_text('VERSION_ID="2026.12"\n', encoding='utf-8')
    env = {**os.environ, 'UNITOTEM_RELEASE_FILE': str(release_file)}
    done = subprocess.run([sys.executable, str(MANAGER / 'main.py'), '--version'], capture_output=True, text=True,
                          timeout=60, cwd=MANAGER, env=env)
    assert done.returncode == 0
    assert done.stdout.strip() == 'unitotem-manager 2026.12'
