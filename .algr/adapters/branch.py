"""algr 自身のドッグフーディングで使うマージのアダプタ。マージ先は統合ブランチ（PR で main に入れるブランチ）。

エージェントは統合ブランチから `algr/<ID>` を切り、結果の pr にそのブランチ名を返す。
マージはクライアントが確かめてから手で行う（`git merge --ff-only algr/<ID>`）。このアダプタは頼まれても何もせず、
ブランチが統合ブランチに入ったかだけを見る。PJ の指摘は統合 PR に来るので、新しいタスクとして起票する（changes は返さない）。
"""

from __future__ import annotations

import subprocess

from algr.adapters import AdapterError, Context, MergeState, MergeStatus
from algr.model import Task


class MergedInto:
    def __init__(self, ctx: Context, into: str = "HEAD") -> None:
        self.root = ctx.root
        self.into = into  # 統合ブランチ。既定はいまチェックアウトしているブランチ

    def _git(self, *args: str) -> int:
        try:
            return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=False).returncode
        except OSError as e:
            raise AdapterError(f"git: {e}") from e

    def request(self, task: Task) -> None:
        pass  # マージはクライアントが手で行う

    def status(self, task: Task) -> MergeStatus:
        if self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{task.pr}") != 0:
            return MergeStatus(MergeState.CLOSED, message=f"branch {task.pr} is gone")
        merged = self._git("merge-base", "--is-ancestor", f"refs/heads/{task.pr}", self.into) == 0
        return MergeStatus(MergeState.MERGED if merged else MergeState.OPEN)
