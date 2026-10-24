# ローカルで結合して試す

本物の Claude Code と k2down で、砂場のリポジトリに 2 件のタスクを流す。見たいのは次の 3 つ。

1. 起票 → 人の回答 → 蒸留 → 実装 → レビュー → マージが一周する
2. 人の答えがルールになり、2 件目の同じ論点は人に聞かずにルールで埋まる（Touchless）
3. `algr stats` と記録（`events.jsonl`、ジョブのログ）で、何が起きたかを後から追える

PR の代わりにローカルのブランチを使い、マージは人が行う（`flow.merge = "human"`）。auto のマージを実際に行う口はまだ無いため。

## 0. 用意するもの

```sh
uv tool install git+https://github.com/yuta4030/algr@claude/great-goodall-fiikna   # algr と algr-agent
uv tool install git+https://github.com/yuta4030/k2down
claude --version                                                                  # ログイン済みであること
```

API を使うので費用がかかる。役ごとの上限は下の設定の `max_budget_usd` で決める。

## 1. 砂場のリポジトリを作る

```sh
mkdir algr-sandbox && cd algr-sandbox
git init -b main
cat > calc.py <<'EOF'
def add(a, b):
    return a + b
EOF
cat > test_calc.py <<'EOF'
from calc import add


def test_add():
    assert add(1, 2) == 3
EOF
printf '.algr/state/\n.algr/tasks/\n__pycache__/\n' > .gitignore
git add -A && git commit -m init
```

`.algr/state/` と `.algr/tasks/` は git に入れない。実装エージェントがブランチを切り替えても、タスクと記録が残るようにするため。

## 2. 設定を書く

`algr init` で `.algr/config.toml` を作ってから、中身を次に置き換える。

```toml
[flow]
max_parallel = 1        # 作業ツリーが 1 つなので、実装は 1 件ずつ
max_rework = 2
merge = "human"         # レビューが通ったら止まり、人がマージする

[adapters.executor]
use = "fake.process"
command = ["algr-agent"]
max_parallel = 1        # 起票・レビューも同じ作業ツリーで動くので 1 つずつ

[adapters.decisions]
use = "k2down"          # .algr/decisions/ に rules.json と usage.jsonl ができる

[agents.default]
max_budget_usd = 1
args = ["--allowedTools", "Bash(git:*)", "Bash(python:*)", "Bash(uv:*)"]

[agents.implement]
permission_mode = "acceptEdits"
max_budget_usd = 2
```

ツールの許可が足りずに止まるときは、砂場に限って `permission_mode = "bypassPermissions"` にする。

実装とレビューに、ローカルのブランチで動くよう追記する（`.algr/prompts/` は利用側の追記）。

```sh
mkdir -p .algr/prompts
cat > .algr/prompts/implement.md <<'EOF'
# この砂場での決まり

- PR は作らない。main から `algr/<タスク ID>` のブランチを切り、そこにコミットする（やり直しなら同じブランチに足す）
- 結果の `pr` にはブランチ名（例：`algr/I1`）を返す
- テストは `python -m pytest -q` で通す
- 終わったら `git checkout main` で main に戻る
EOF
cat > .algr/prompts/review.md <<'EOF'
# この砂場での決まり

- `pr` はローカルのブランチ名。`git diff main...<ブランチ>` で差分を見る。ブランチは切り替えない
- テストは `git worktree add ../review-<タスク ID> <ブランチ>` で別のディレクトリに出して `python -m pytest -q` で確かめ、終わったら `git worktree remove` する
EOF
```

## 3. 1 件目：人が答え、ルールになる

ターミナルを 2 つ使う。A で常駐させ、B で人として操作する。

```sh
# A
algr watch --interval 3
```

```sh
# B
algr new "引き算の関数 sub を足す" --purpose "calc に sub(a, b) を足し、テストも書く" \
  -q "error: 数値でない入力が来たときどうするか"
```

起票エージェントが走り、ルールが無いので人に聞いてくる（A に `I1 decision.requested`）。

```sh
algr show I1                                     # 起票エージェントが足した未決事項もここに出る
algr answer I1 I1-1="TypeError を投げる"          # 残りの未決事項も同じように答える
```

答えると起票に戻り、蒸留・実装・レビューが流れる。レビューが通ると `[review, approved]` で止まる。

```sh
algr proposals                                   # 蒸留エージェントのルール提案
algr accept P001                                 # 提案をルールにする（k2down add）
k2down --dir .algr/decisions list
git add .algr/decisions && git commit -m "rule: P001"   # ルールの PR のマージにあたる

git log --oneline main..algr/I1 && git diff main...algr/I1
git merge algr/I1
algr merge I1                                    # done にし、ルールに従った判断を hit として記録する
```

## 4. 2 件目：同じ論点がルールで埋まる

```sh
algr new "割り算の関数 div を足す" --purpose "calc に div(a, b) を足し、テストも書く" \
  -q "error: 数値でない入力が来たときどうするか"
```

`decision.requested` を挟まずに実装まで進めば成功。`algr show I2` で、その未決事項が `[R001]` で埋まっていることを見る。
レビューが通ったら 1 件目と同じようにマージする。

## 5. 振り返る

```sh
algr stats                                       # 差し戻し率、STP 率、介入回数、判断待ち、トークン数
cat .algr/state/notifications.jsonl              # 人に届いた通知（通知はスタブなのでファイル）
less .algr/state/events.jsonl                    # イベントとジョブの記録
ls .algr/state/runs/                             # ジョブごとの job.json と log（エージェントの出力）
k2down --dir .algr/decisions report              # ルールの当たり率
```

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| `I1 failed: exit 1` | `.algr/state/runs/<ジョブ ID>/log`。claude のエラーか、ツールの許可が足りない |
| `bad result from <役>` | 同じ log。エージェントが結果の JSON を返していない、形が違う |
| 実装が何度も修正依頼で戻る | `algr show` の `!` 行（修正依頼）。`max_rework` を超えると failed |
| 2 件目でも人に聞かれる | `algr rules` にルールがあるか。起票エージェントが論点を同じと見なしたか（`.algr/state/runs/I2.intake.*/log`） |

failed は `algr reopen I1` で起票からやり直せる。最初からやり直すときは `rm -rf .algr/tasks .algr/state` と `git checkout main`。

## 報告してほしいこと

- どこまで進んだか、止まったところの `log`
- `algr stats` の出力
- プロンプト（`src/algr/prompts/`）の効き具合で気になったところ（聞きすぎ、決めすぎ、結果の形を外す、など）
