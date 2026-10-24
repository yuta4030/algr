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
algr list --state needs-decision
algr answer I1 I1-1="OAuth"                        # 答えると起票に戻り、ready になる
algr tick                                          # トリガーを取り込み、実行を見て、ready を配車する
```

`--json` を付けると JSON を返します。エラー時は終了コード 1 で、stderr に `error: ...` を書きます。

## フロー

タスクは 7 つの状態を移り、遷移ごとにイベントが 1 つ出ます。イベントがそのまま hook の差し込み口です。

```text
主経路   intake ─▶ ready ─▶ in-progress ─▶ review ─▶ done
人に聞く intake / in-progress / review ─▶ needs-decision ─▶ intake
失敗     in-progress / review ─▶ failed
```

人が関わるのは needs-decision と failed だけです。人が答えると必ず起票に戻り、そこから主経路に合流します。

| イベント | 既定の処理 |
|---|---|
| `task.created` | 起票エージェントがルールを当て、決まる点を埋める |
| `decision.requested` | 非同期（`source: auto`）のときだけ通知する |
| `decision.resolved` | 判断を記録し、ルール化を提案し、起票をやり直す |
| `task.ready` | 配車を待つ |
| `task.started` | 実行アダプタの `start` を呼ぶ |
| `task.returned` | 差し戻しを記録し、人に知らせる |
| `task.submitted` | レビューエージェントを起動する |
| `review.escalated` | 異議のある判断を未決に戻し、人に回す |
| `task.done` | ルールに従った判断を hit として記録する |
| `task.failed` | 人に上げる |

遷移ごとに不変条件があります（例：ready にするには未決事項がゼロ、ルールで決めた判断にはルールの ID が付いている）。

## 設定とアダプタ

リポジトリの `.algr/config.toml` に、個人の `~/.algr/config.toml`（`ALGR_HOME` で変更可）を重ねます。個人の設定が勝ちます。

```toml
[flow]
max_parallel = 1

[adapters.task]
use = "fake.tasks"                      # 組み込み名
[adapters.executor]
use = "fake.process"
command = ["claude", "-p", "..."]
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
| `executor` | 実装エージェントを起動する（`start` / `status` / `capacity`） | `fake.process`：ローカルのコマンド |
| `decisions` | ルールの提示、判断の記録と集計（k2down 互換の操作） | `fake.rules`：rules.json と usage.jsonl |
| `notify` | 人の判断待ちを届ける | `stub`：ファイルに追記 |

`fake.process` のコマンドには `ALGR_TASK_ID` / `ALGR_TASK_FILE` / `ALGR_RESULT_FILE` が渡ります。
コマンドは `ALGR_RESULT_FILE` に `{"pr": "...", "decisions": [{"text", "answer", "topic"?, "rule_ids"?}]}`、
または差し戻すときは `{"returned": {"text", "topic"?, "recommended"?}}` を書いて終了します。

## 開発

```sh
uv run pytest -q
```

## ライセンス

MIT
