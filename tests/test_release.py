import subprocess
import sys
from pathlib import Path

import algr

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_notes.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8")


def test_current_version_has_changelog_section():
    """版を上げたら CHANGELOG も書く。CI はこの節を GitHub Release の本文にする。"""
    assert run("--version").stdout.strip() == algr.__version__
    result = run(algr.__version__)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
    assert "## [" not in result.stdout  # 次の版の節まで含めない


def test_unknown_version_fails():
    result = run("0.0.0")
    assert result.returncode == 1 and "0.0.0" in result.stderr
