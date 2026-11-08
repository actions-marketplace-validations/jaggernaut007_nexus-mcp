"""benchmarks/install_nexus.sh refuses a label that is not a plain folder name.

The label becomes `benchmarks/.venv-<label>`, which the script deletes with `rm -rf`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "benchmarks" / "install_nexus.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("label", ["../x", "a/b", "..", ".", "", "x y", "$(id)"])
def test_install_nexus_refuses_a_label_with_path_parts(label):
    proc = subprocess.run(["bash", str(SCRIPT), "HEAD", label],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2
    assert "bad label" in proc.stderr
