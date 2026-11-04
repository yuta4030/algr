"""リリース用の補助。CI（.github/workflows/release.yml）とテストから使う。

    python3 scripts/release_notes.py --version   # pyproject.toml の版を出す
    python3 scripts/release_notes.py 0.4.0       # CHANGELOG.md のその版の節を出す（なければ終了コード 1）
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def release_notes(version: str, changelog: str) -> str | None:
    """「## [版] - 日付」の見出しから次の「## 」の手前までを返す。"""
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL)
    return None if match is None else match.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print(project_version())
        return 0
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    notes = release_notes(argv[0], (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    if notes is None:
        print(f"CHANGELOG.md has no section for {argv[0]}", file=sys.stderr)
        return 1
    print(notes, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
