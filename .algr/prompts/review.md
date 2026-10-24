# algr リポジトリでの決まり

- `pr` はローカルのブランチ名。`git diff HEAD...<ブランチ>` で差分を見る。ブランチは切り替えない
- テストは `git worktree add ../review-<タスク ID> <ブランチ>` で別のディレクトリに出して `uv run pytest -q` で確かめ、終わったら `git worktree remove` する
- CLAUDE.md の「絶対に守る制約」に反する差分は異議にする
