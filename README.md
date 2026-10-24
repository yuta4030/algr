# algr

algr（/ˈælɡər/、アルガー）は、Algorithmic Development という働き方を回すためのツールです。
人は戦略（ルール）を書き、タスクはルールで判断を埋めたうえで、人の手に触れずにマージまで流れます。人は例外だけを受けます。

- **メタの層（アルゴリズミック）**：人とAIが、処理を動かすルールとフローそのものを書き換える
- **処理の層（STP）**：起票 → 実装 → レビュー → マージを、ルールに従って流す

いまはフェーズ0（境界を引く）です。algr 本体とフェイクアダプタだけで、ループが一周します。

## 必要なもの

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

依存パッケージはゼロです。外部との接点はすべてアダプタに置きます。

## 使い方

```sh
uv tool install git+https://github.com/yuta4030/algr
cd your-repo
algr init                                          # .algr/config.toml をフェイクアダプタで作る
algr new "ログインを足す" -q "auth: どうサインインさせるか"
algr run                                           # 走っているジョブがなくなるまで回す
algr list --state needs-decision
algr answer I1 I1-1="OAuth"                        # 答えると起票に戻る
algr run                                           # 起票 → 実装 → レビュー → マージ
algr new "ログアウトを足す" --depends I1             # I1 が done になるまで配車しない
algr reopen I2 --note "runner を直した"            # failed のタスクを起票に戻す
algr merge I3                                      # マージのゲート（flow.merge = "human"）で承認済みをマージする
algr proposals                                     # 蒸留エージェントのルール提案
algr accept P001                                   # 提案をルールにする（algr decline で却下）
```

`algr stats` は `.algr/state/events.jsonl` から指標を集計します。実装中とレビューでの差し戻し率、修正依頼率、
STP 率（人の回答・やり直し・マージが一度も入らずに done になった割合）、タスクあたりの人の介入回数、人の判断にかかる時間の中央値、
タスクあたりのトークン数を出します。`events.jsonl` にはイベントのほか、終わったジョブ（`job`）と hook の失敗（`hook_error`）も残ります。

`algr tick` は 1 周だけ回します（トリガーを取り込み、ジョブの結果を受け取り、ready を配車し、要るジョブを起動する）。

`--json` を付けると JSON を返します。エラー時は終了コード 1 で、stderr に `error: ...` を書きます。

## フロー

タスクは 7 つの状態を移り、遷移ごとにイベントが 1 つ出ます。イベントがそのまま hook の差し込み口です。

```text
主経路     intake ─▶ ready ─▶ in-progress ─▶ review ─▶ done
人に聞く   intake / review ─▶ needs-decision ─▶ intake
差し戻し   in-progress ─▶ intake（起票エージェントがルールを当て、埋まらなければ人に聞く）
修正依頼   review ─▶ in-progress（flow.max_rework 回まで。超えたら failed）
失敗       intake / in-progress / review ─▶ failed ─▶ intake（人が reopen する）
```

人が関わるのは needs-decision と failed、それにマージのゲートを置いたとき（`flow.merge = "human"`）の承認済みの review だけです。
人が答えると必ず起票に戻り、そこから主経路に合流します。人に聞くときは必ず通知します。

イベント名とペイロード（`Event.data`）の形は固定です（`src/algr/flow.py` の `TRANSITIONS` と `EVENT_DATA`）。
どのイベントにも遷移元の状態 `from` が付き（`task.created` は `""`）、決断 ID の並びは必ず `questions` で渡します。

| イベント | ペイロード | 既定の処理 |
|---|---|---|
| `task.created` | `source`（`human` / `auto`） | 起票エージェントを起動する |
| `decision.requested` | `channel`（`inline` / `queue`）、`questions` | 人に知らせる（channel は聞き方の違いだけ） |
| `decision.resolved` | `questions`（人が答えたもの） | 起票エージェントと蒸留エージェントを起動する |
| `task.ready` | — | 配車を待つ |
| `task.started` | — | 実装エージェントを起動する |
| `task.returned` | `questions` | 起票エージェントを起動する（ルールで埋まらなければ人に聞く） |
| `task.submitted` | `pr` | レビューエージェントを起動する |
| `review.changes_requested` | `changes`（修正依頼） | 実装エージェントを起動する（前の PR と指摘を渡す） |
| `review.escalated` | `questions`、`reasons`（決断 ID → 異議） | 異議のある判断を未決事項として起票に差し戻し、人に知らせる |
| `task.done` | `pr`、`merged_by`（`auto` / `human`） | ルールに従った判断を hit として記録する |
| `task.failed` | `role`、`message` | 人に上げる |
| `task.reopened` | `note` | 起票エージェントを起動する |

レビューが通ると、既定ではそのまま `task.done` になります。`flow.merge = "human"` にすると、タスクは承認済み（`approved`）のまま review に留まり、
人に知らせて `algr merge`（またはトリガーの `task.done`）を待ちます。人が答えた未決事項は人のゲートを通っているので、再レビューで異議が出ても蒸し返しません。

トリガーが外から送れるのは、人の操作と同じ `task.created` / `decision.resolved` / `task.reopened` / `task.done` だけです。

### hook

hook は `.algr/config.toml` の `[hooks]` に、イベント名（すべてのイベントを受けるなら `"*"`）ごとに並べます。

- 形は `hook(event, engine)`。`event.data` は上の表の形です。タスクは `engine.tasks.get(event.task_id)` で読み直します
- 呼ばれるのは状態を保存し、既定の処理を済ませたあとです。hook は遷移を止められません（止める判断はエージェントと不変条件が持ちます）
- hook の中で `engine.emit` したイベントは、今のイベントの hook がすべて済んでから処理します
- hook が例外を投げてもフローは止めません。`.algr/state/events.jsonl` に `hook_error` を残し、通知アダプタで人に知らせます
- 知らないイベント名の hook は、設定の読み込みでエラーにします
- イベントは再送されることがあるので、hook もアダプタと同じく冪等に書きます

遷移ごとに不変条件があります（例：ready にするには未決事項がゼロ、ルールで埋めた未決事項にはルールの ID が付いている）。

タスクは 2 種類の判断を別々に持ちます。どちらも決断 ID（`I1-1`）をタスク内で通し番号で振ります。

- **未決事項**（`questions`）：起票で埋めるもの。ルールで埋まるか、人が答える
- **判断**（`decisions`）：実装エージェントが実装中に下し、PR に載せるもの。ルールに従ったか、根拠なく決めたか。PR を出し直すと新しい一覧で置き換わる

タスクは `depends` に依存先のタスク ID を持てます。依存先がすべて done になるまで、ready のまま配車しません。
依存先が failed になったら、待っているタスクは待ち続け、失敗の通知に待っているタスクの ID を添えます（直すのは失敗した方なので）。

タスクの保存は楽観ロックです。`TaskAdapter.save` は読んだときの版（`version`）と保存済みの版が違えば `Conflict` を投げ、
エンジンはそのタスクを次の tick で読み直してやり直します。

## エージェント

起票・実装・レビュー・蒸留のエージェントは、すべて実行アダプタがジョブとして起動します。
エージェントは数分から数十分かかるので、コアは `start` してすぐ戻り、次の tick で `status` から結果を受け取ります。
どの状態でどのエージェントが要るかはコアが決め、capacity が空いていなければ次の tick に回します。

| 役 | 起動する時 | 結果（JSON） |
|---|---|---|
| `intake` | intake にいる間 | `{"fills": [{"question_id", "answer", "rule_ids"}], "questions": [{"text", "topic"?, "options"?, "recommended"?}]}` |
| `implement` | in-progress にいる間 | `{"pr", "decisions": [{"text", "answer", "topic"?, "rule_ids"?}]}`、差し戻すときは `{"returned": {"text", "topic"?, "recommended"?}}` |
| `review` | review にいる間（承認済みを除く） | `{"approved": bool, "objections": {"<決断 ID>": "<理由>"}, "changes"?: ["<修正依頼>"]}` |
| `distill` | 人が答えた未決事項がある時 | `{"rules": [{"rule", "source"}]}` |

`intake` と `implement` の結果には、見つけた別件を起票する `"tasks": [{"title", "purpose"?, "questions"?, "depends"?, "priority"?}]` を付けられます。
別件は `source: auto` で起票され、`depends` には既にあるタスク（このタスクを含む）の ID を書きます。

`objections` は判断への異議で、人に回します。`changes` はテスト失敗やバグなど判断以外の指摘で、実装に戻します。
ジョブの入力（`input`）には今のルールが入り、実装には前の回の PR と判断（`previous`）と修正依頼（`feedback`）、
レビューには人が決めた未決事項の ID（`settled`）も入ります。やり直しは一から作らず、差分だけ直す前提です。

結果の形が違えば、ジョブは失敗として扱います。形の定義は `src/algr/agents.py` にあります。

## 設定とアダプタ

リポジトリの `.algr/config.toml` に、個人の `~/.algr/config.toml`（`ALGR_HOME` で変更可）を重ねます。個人の設定が勝ちます。

```toml
[flow]
max_parallel = 1                        # 同時に in-progress にするタスクの数
max_rework = 2                          # レビューの修正依頼で実装に戻す回数の上限
merge = "auto"                          # "human" でマージに人のゲートを置く

[adapters.task]
use = "fake.tasks"                      # 組み込み名
[adapters.executor]
use = "fake.process"
command = ["claude", "-p", "..."]       # 省略するとフェイクのエージェント
max_parallel = 2                        # ジョブを同時に走らせる数
[adapters.notify]
use = ".algr/adapters/slack.py:Slack"   # リポジトリに置いたアダプタ
channel = "#dev"

[agents.review]
model = "small"                         # 役ごとの設定。ジョブの input.agent として実行アダプタに渡す
max_tokens = 20000

[hooks]
"task.done" = [".algr/hooks.py:on_done"]  # hook(event, engine)
"*" = [".algr/hooks.py:log"]              # すべてのイベント
```

アダプタは `Class(ctx, **options)` で作られます。IF は `src/algr/adapters/__init__.py` にあります。

| アダプタ | 役割 | 組み込み（フェイク） |
|---|---|---|
| `task` | タスクの読み書きとコメント | `fake.tasks`：1 件 1 ファイルの JSON |
| `trigger` | 外部のイベントをフローのイベントに変換する | `fake.inbox`：ディレクトリに置いた JSON |
| `executor` | エージェントをジョブとして起動する（`start(job)` / `status(job_id)` / `capacity()`） | `fake.process`：ローカルのコマンド |
| `decisions` | ルールの提示、判断の記録と集計、ルールの提案（k2down 互換の操作） | `fake.rules`：rules.json と usage.jsonl。`k2down`：k2down の CLI |
| `notify` | 人の判断待ちを届ける | `stub`：ファイルに追記 |

`fake.process` のコマンドには `ALGR_JOB_ID` / `ALGR_ROLE` / `ALGR_JOB_FILE` / `ALGR_RESULT_FILE` が渡ります。
`ALGR_JOB_FILE` には `{"id", "role", "task", "input"}` が入っていて、コマンドは役ごとの結果を `ALGR_RESULT_FILE` に書いて終了します。
結果に `"usage": {"input_tokens", "output_tokens"}` を添えると、使ったトークン数としてタスクの `usage` に足し上げ、`algr stats` の `tokens_per_task` に出します。
`command` を省略すると、AI を使わず決まりで判断するフェイクのエージェント（`python -m algr.fake_agent`）が走ります。

### k2down

判断ストアの標準は [k2down](https://github.com/yuta4030/k2down) です（`uv tool install git+https://github.com/yuta4030/k2down`）。

```toml
[adapters.decisions]
use = "k2down"
dir = ".algr/decisions"                 # rules.json と usage.jsonl を置くところ（k2down の --dir）
command = ["k2down"]                    # 省略時。uvx で動かすなら ["uvx", "--from", "git+https://github.com/yuta4030/k2down", "k2down"]
```

蒸留エージェントの提案は `.algr/state/proposals.json` に積み、`algr accept` したときに `k2down add` で rules.json に書きます。
rules.json の差分を PR にしてマージすることを、人の確認とします。

## 開発

```sh
uv run pytest -q
```

## ライセンス

MIT
