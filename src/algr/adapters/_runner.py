"""LocalProcess が使う小さな包み。コマンドを走らせ、終了コードをファイルに残す。

使い方：python -m algr.adapters._runner RUN_DIR -- COMMAND...
"""

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    run_dir = Path(argv[0])
    command = argv[argv.index("--") + 1 :]
    with (run_dir / "log").open("w", encoding="utf-8") as log:
        code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    tmp = run_dir / "exit.tmp"
    tmp.write_text(str(code), encoding="utf-8")
    tmp.replace(run_dir / "exit")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
