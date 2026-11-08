"""--version is answered by main.py before any configuration is read (a subprocess: nothing of the manager starts)."""
import subprocess
import sys
from pathlib import Path

import utils.constants as const

MANAGER = Path(__file__).resolve().parents[1]


def test_version_prints_the_version_and_exits_zero():
    done = subprocess.run([sys.executable, str(MANAGER / 'main.py'), '--version'], capture_output=True, text=True,
                          timeout=60, cwd=MANAGER)
    assert done.returncode == 0
    assert done.stdout.strip() == f'unitotem-manager {const.__version__}'
