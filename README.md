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
```

`algr tick` は 1 周だけ回します（トリガーを取り込み、ジョブの結果を受け取り、ready を配車し、要るジョブを起動する）。

`--json` を付けると JSON を返します。エラー時は終了コード 1 で、stderr に `error: ...` を書きます。

## フロー

タスクは 7 つの状態を移り、遷移ごとにイベントが 1 つ出ます。イベントがそのまま hook の差し込み口です。

```text
主経路   intake ─▶ ready ─▶ in-progress ─▶ review ─▶ done
人に聞く intake / in-progress / review ─▶ needs-decision ─▶ intake
失敗     intake / in-progress / review ─▶ failed
```

人が関わるのは needs-decision と failed だけです。人が答えると必ず起票に戻り、そこから主経路に合流します。

| イベント | 既定の処理 |
|---|---|
| `task.created` | 起票エージェントを起動する |
| `decision.requested` | 非同期（`source: auto`）のときだけ通知する |
| `decision.resolved` | 起票エージェントと蒸留エージェントを起動する |
| `task.ready` | 配車を待つ |
| `task.started` | 実装エージェントを起動する |
| `task.returned` | 人に知らせる |
| `task.submitted` | レビューエージェントを起動する |
| `review.escalated` | 異議のある判断を未決事項として起票に差し戻し、人に知らせる |
| `task.done` | ルールに従った判断を hit として記録する |
| `task.failed` | 人に上げる |

遷移ごとに不変条件があります（例：ready にするには未決事項がゼロ、ルールで埋めた未決事項にはルールの ID が付いている）。

タスクは 2 種類の判断を別々に持ちます。どちらも決断 ID（`I1-1`）をタスク内で通し番号で振ります。

- **未決事項**（`questions`）：起票で埋めるもの。ルールで埋まるか、人が答える
- **判断**（`decisions`）：実装エージェントが実装中に下し、PR に載せるもの。ルールに従ったか、根拠なく決めたか。実装をやり直すと捨てられる

## エージェント

起票・実装・レビュー・蒸留のエージェントは、すべて実行アダプタがジョブとして起動します。
エージェントは数分から数十分かかるので、コアは `start` してすぐ戻り、次の tick で `status` から結果を受け取ります。
どの状態でどのエージェントが要るかはコアが決め、capacity が空いていなければ次の tick に回します。

| 役 | 起動する時 | 結果（JSON） |
|---|---|---|
| `intake` | intake にいる間 | `{"fills": [{"question_id", "answer", "rule_ids"}], "questions": [{"text", "topic"?, "options"?, "recommended"?}]}` |
| `implement` | in-progress にいる間 | `{"pr", "decisions": [{"text", "answer", "topic"?, "rule_ids"?}]}`、差し戻すときは `{"returned": {"text", "topic"?, "recommended"?}}` |
| `review` | review にいる間 | `{"approved": bool, "objections": {"<決断 ID>": "<理由>"}}` |
| `distill` | 人が答えた未決事項がある時 | `{"rules": [{"rule", "source"}]}` |

結果の形が違えば、ジョブは失敗として扱います。形の定義は `src/algr/agents.py` にあります。

## 設定とアダプタ

リポジトリの `.algr/config.toml` に、個人の `~/.algr/config.toml`（`ALGR_HOME` で変更可）を重ねます。個人の設定が勝ちます。

```toml
[flow]
max_parallel = 1                        # 同時に in-progress にするタスクの数

[adapters.task]
use = "fake.tasks"                      # 組み込み名
[adapters.executor]
use = "fake.process"
command = ["claude", "-p", "..."]       # 省略するとフェイクのエージェント
max_parallel = 2                        # ジョブを同時に走らせる数
[adapters.notify]
use = ".algr/adapters/slack.py:Slack"   # リポジトリに置いたアダプタ
channel = "#dev"

[hooks]
"task.done" = [".algr/hooks.py:on_done"]  # hook(event, engine)
```

アダプタは `Class(ctx, **options)` で作られます。IF は `src/algr/adapters/__init__.py` にあります。

| アダプタ | 役割 | 組み込み（フェイク） |
|---|---|---|
| `task` | タスクの読み書きとコメント | `fake.tasks`：1 件 1 ファイルの JSON |
| `trigger` | 外部のイベントをフローのイベントに変換する | `fake.inbox`：ディレクトリに置いた JSON |
| `executor` | エージェントをジョブとして起動する（`start(job)` / `status(job_id)` / `capacity()`） | `fake.process`：ローカルのコマンド |
| `decisions` | ルールの提示、判断の記録と集計（k2down 互換の操作） | `fake.rules`：rules.json と usage.jsonl |
| `notify` | 人の判断待ちを届ける | `stub`：ファイルに追記 |

`fake.process` のコマンドには `ALGR_JOB_ID` / `ALGR_ROLE` / `ALGR_JOB_FILE` / `ALGR_RESULT_FILE` が渡ります。
`ALGR_JOB_FILE` には `{"id", "role", "task", "input"}` が入っていて、コマンドは役ごとの結果を `ALGR_RESULT_FILE` に書いて終了します。
`command` を省略すると、AI を使わず決まりで判断するフェイクのエージェント（`python -m algr.fake_agent`）が走ります。

## 開発

```sh
uv run pytest -q
```

## ライセンス

MIT
