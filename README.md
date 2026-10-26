# algr

algr（/ˈælɡər/、アルガー）は、Algorithmic Development という働き方を回すためのツールです。
人は戦略（ルール）を書き、タスクはルールで判断を埋めたうえで、人の手に触れずにマージまで流れます。人は例外だけを受けます。

- **メタの層（アルゴリズミック）**：人とAIが、処理を動かすルールとフローそのものを書き換える
- **処理の層（STP）**：起票 → 実装 → レビュー → マージを、ルールに従って流す

いまはフェーズ1です。Claude Code（`algr-agent`）と k2down で実タスクを流し、algr 自身も algr で開発しています（[docs/dogfooding.md](docs/dogfooding.md)）。

## 人の役割

人は処理の中から抜け、処理の外側で 2 つの立場に立ちます。

| 立場 | やること | 答える判断 | 操作 |
|---|---|---|---|
| クライアント | 何をやるか、目的、こだわり度を伝える | 「何を大事にするか」が問われるもの | `algr new`、`algr answer`、`algr depends` |
| メタプログラマー | 何を自動で通してよいかを決める。ルールを見直し、他チームとの境界（公開する IF や API の契約）を守る | 「どこまで任せてよいか」が問われるもの | `algr distill`、`algr accept` / `decline`、`algr merge`、`algr reopen` |

ルールで決まらない判断は、文脈を一番持っている起票の瞬間に寄せます。人が答えた判断は蒸留エージェントがルールにし、
メタプログラマーが受け入れたものだけがルールになります。使うほど人に上がる判断は減り、起票でも人が答えない状態（Touchless）に近づきます。

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
algr depends I2 --add I3 --remove I1              # 依存先をあとから足す・外す
algr reopen I2 --note "runner を直した"            # failed のタスクを起票に戻す
algr merge I3                                      # マージのゲート（flow.merge = "human"）で承認済みをマージする
algr distill "日付は UTC で保存する" --topic time  # タスクの外で言った方針を、蒸留エージェントに依頼する
algr proposals                                     # 蒸留エージェントのルール提案（足す提案と外す提案）
algr accept P-a2m4n                                # 提案をルールにする。外す提案ならルールを外す（algr decline で却下）
```

`algr distill` は方針を `source: policy` のタスクに乗せ、蒸留のジョブだけを出します。タスクは最初から done で、起票・実装・レビューには流れず、
イベントも出しません（`algr stats` にも数えません）。蒸留エージェントは既存のルールと突き合わせ、同じ趣旨なら提案せず、
ぶつかるなら新しいルールと、古いルールを外す提案を出します。どちらも人が `algr accept` するまでルールには効きません。

`algr stats` は `.algr/state/events.jsonl` から指標を集計します。実装中とレビューでの差し戻し率、修正依頼率、
STP 率（人の回答・やり直し・マージが一度も入らずに done になった割合）、タスクあたりの人の介入回数、人の判断にかかる時間の中央値、
タスクあたりのトークン数（合計と、キャッシュの読み込み・書き込みを分けた項目ごと）を出します。`events.jsonl` にはイベントのほか、ジョブの起動（`job_started`）と終了（`job`。使ったトークン数の `usage` と往復の回数の `turns` を持つ）、hook の失敗（`hook_error`）も時刻付きで残ります。

`algr watch` は常駐して tick を回し続けます（`--interval` 秒ごと）。トリガーの取り込みと ready の配車が止まらないようにするためのプロセスで、
起きたイベントを 1 行ずつ出します。SIGINT（Ctrl-C）か SIGTERM で、いまの tick を終えてから止まります。
保存は楽観ロックなので、`algr answer` などと同時に動かしてかまいません。

`algr ui` は現状を見るページを `http://127.0.0.1:8765/` で開きます（`--port` で変えられます）。あなたの番（回答・やり直し・マージ）、
タスクごとの進み具合といま誰の手にあるか、最近のイベント、指標、ルールと提案を 3 秒ごとに読み直して出します。
サブページは、推移（起票した週ごとの差し戻し率・介入回数・STP 率など）、ルール（使われた回数、提案、まだルールでない判断）、
フロー（遷移ごとの通過回数と状態ごとの滞在時間）、ジョブ（タスクごとのジョブの時間とトークン数）、ヘルプの 5 つです。
ヘルプは algr 自身の説明です。大きく変わらないもの（コンセプト、人の立場、ID）は `ui.html` に書き、状態・遷移・不変条件・役ごとの結果の形・プロンプト・コマンドのリファレンスはコードから組み立てます（`ui.help_page`）。
図（2 つの層、状態とイベント、ルールを育てるループ）は `src/algr/diagrams.py` に箱と矢印のデータで持ち、SVG に書き出します。人・AI・データで形と色を分けています。状態とイベントの図はテストで `TRANSITIONS` と突き合わせます。
説明の文はコードの横（`flow.STATE_HELP` / `EVENT_HELP`、`agents.ROLE_HELP`、`cli.STANCES`）に 1 か所だけ置き、このファイルの表はテスト（`tests/test_help.py`）でそれと突き合わせます。
読むのはアダプタの IF とイベントログだけで、GUI は判断も状態も持ちません。いまは閲覧だけで、操作のボタンには同じことをする CLI を添えています。

`algr tick` は 1 周だけ回します（トリガーを取り込み、ジョブの結果を受け取り、ready を配車し、要るジョブを起動する）。

`--json` を付けると JSON を返します。エラー時は終了コード 1 で、stderr に `error: ...` を書きます。

## フロー

タスクは 8 つの状態を移り、遷移ごとにイベントが 1 つ出ます。イベントがそのまま hook の差し込み口です。

```text
主経路     intake ─▶ ready ─▶ in-progress ─▶ review ─▶ done
承認       review ─▶ approved ─▶ done（マージのゲートのとき。approved から出るのは done と failed だけ）
人に聞く   intake / review ─▶ needs-decision ─▶ intake
差し戻し   in-progress ─▶ intake（起票エージェントがルールを当て、埋まらなければ人に聞く）
修正依頼   review ─▶ in-progress（flow.max_rework 回まで。超えたら failed）
失敗       intake / in-progress / review / approved ─▶ failed ─▶ intake（人が reopen する）
```

人が関わるのは needs-decision と failed、それにマージのゲートを置いたとき（`flow.merge = "human"`）の approved だけです。
人が答えると必ず起票に戻り、そこから主経路に合流します。人に聞くときは必ず通知します。
failed になった理由（`role` と `message`）はタスクの `failure` に残り、`algr show` で読めます。reopen すると消えます。
エージェントが落ちたときの `message` には、エージェントの出力（`algr.agent` なら claude の `result` や `is_error`）の最後の方が入ります。

イベント名とペイロード（`Event.data`）の形は固定です（`src/algr/flow.py` の `TRANSITIONS` と `EVENT_DATA`）。
どのイベントにも遷移元の状態 `from` が付き（`task.created` は `""`）、決断 ID の並びは必ず `questions` で渡します。

| イベント | ペイロード | 既定の処理 |
|---|---|---|
| `task.created` | `source`（`human` / `auto`） | 起票エージェントを起動する |
| `decision.requested` | `channel`（`inline` / `queue`）、`questions` | 人に知らせる（channel は聞き方の違いだけ） |
| `decision.resolved` | `questions`（人が答えたもの） | 起票エージェントを起動する |
| `task.ready` | — | 配車を待つ |
| `task.started` | — | 実装エージェントを起動する |
| `task.returned` | `questions` | 起票エージェントを起動する（ルールで埋まらなければ人に聞く） |
| `task.submitted` | `pr` | レビューエージェントを起動する |
| `review.changes_requested` | `changes`（修正依頼） | 実装エージェントを起動する（前の PR と指摘を渡す） |
| `review.escalated` | `questions`、`reasons`（決断 ID → 異議） | 異議のある判断を未決事項として起票に差し戻し、人に知らせる |
| `review.approved` | `pr` | マージのゲート（`flow.merge = "human"`）のときだけ。人のマージを待つと知らせる（`algr watch` にも出る） |
| `task.done` | `pr`、`merged_by`（`auto` / `human`） | ルールに従った判断を hit として記録し、人が答えた未決事項があれば蒸留エージェントを起動する |
| `task.failed` | `role`、`message` | 人に上げる |
| `task.reopened` | `note` | 起票エージェントを起動する |

レビューが通ると、既定ではそのまま `task.done` になります。`flow.merge = "human"` にすると、タスクは`approved` 状態で止まり、
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

### 不変条件とタスクの持ち物

遷移ごとに不変条件があります（例：ready にするには未決事項がゼロ、ルールで埋めた未決事項にはルールの ID が付いている）。

タスクは 2 種類の判断を別々に持ちます。どちらも決断 ID（`I1-1`）をタスク内で通し番号で振ります。

- **未決事項**（`questions`）：起票で埋めるもの。ルールで埋まるか、人が答える
- **判断**（`decisions`）：実装エージェントが実装中に下し、PR に載せるもの。ルールに従ったか（`rule_ids`）、未決事項の答えに従ったか（`question_ids`。根拠は未決事項の by を受け継ぐ）、根拠なく決めたか。PR を出し直すと新しい一覧で置き換わる

タスクは `depends` に依存先のタスク ID を持てます。依存先がすべて done になるまで、ready のまま配車しません。
依存先は起票のとき（`algr new --depends`）に付け、あとから `algr depends` で足す・外せます。依存先は実在するタスクに限り、循環する依存は付けられません。
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
| `implement` | in-progress にいる間 | `{"pr", "decisions": [{"text", "answer", "topic"?, "rule_ids"?, "question_ids"?}]}`、差し戻すときは `{"returned": {"text", "topic"?, "recommended"?}}` |
| `review` | review にいる間 | `{"approved": bool, "objections": {"<決断 ID>": "<理由>"}, "changes"?: ["<修正依頼>"]}` |
| `distill` | done になったタスクに、人が答えた未決事項がある時（1 タスク 1 回。`algr distill` の方針はすぐ） | `{"rules": [{"rule", "source"?}], "retire"?: [{"rule_id", "reason", "source"?}]}` |

`intake` と `implement` の結果には、見つけた別件を起票する `"tasks": [{"title", "purpose"?, "questions"?, "depends"?, "priority"?}]` を付けられます。
別件は `source: auto` で起票され、`depends` には既にあるタスク（このタスクを含む）の ID を書きます。

`objections` は判断への異議で、人に回します。`changes` はテスト失敗やバグなど判断以外の指摘で、実装に戻します。
ジョブの入力（`input`）には今のルールが入り、実装には前の回の PR と判断（`previous`）と修正依頼（`feedback`）、
レビューには人が決めた未決事項の ID（`settled`）、蒸留には判断ストアの手入れの候補（`report`）も入ります。やり直しは一から作らず、差分だけ直す前提です。

蒸留は人の答えのほか、`report` の落とす候補（一度も当たらないルール）・書き直す候補（外れや異議の多いルール）・ぶつかる組から、
外す提案と言い直し（新しいルールと外す提案の対）を出します。`report` から出した提案は、元になった決断 ID（`source`）を持ちません。
判断ストアが `report()` を持たなければ、`report` は空です（k2down とフェイクは持っています）。

結果の形が違えば、ジョブは失敗として扱います。形の定義は `src/algr/agents.py` にあります。

### Claude Code で動かす

`algr-agent`（`python -m algr.agent`）は、役のプロンプトとジョブを Claude Code（`claude -p --output-format json`）に渡し、
答えの最後の ```json ブロックを結果として書き戻すコマンドです。使ったトークン数も結果に添えます。

```toml
[adapters.executor]
use = "fake.process"
command = ["algr-agent"]                # python -m algr.agent と同じ
max_parallel = 2

[agents.default]
permission_mode = "acceptEdits"         # claude の --permission-mode
max_budget_usd = 2                      # claude の --max-budget-usd
[agents.review]
model = "haiku"                         # claude の --model。ほかに command（既定 ["claude"]）と args を書ける
```

プロンプトは変わりにくいものから順に並べ、共通部分のキャッシュを効かせます。

1. algr 共通（`src/algr/prompts/common.md`）：守ること、安全網のルール、結果の返し方
2. 役（`src/algr/prompts/<役>.md`）
3. 利用側の追記（`.algr/prompts/common.md`、`.algr/prompts/<役>.md`）。社内固有のことはここに書く
4. ジョブ（タスクの要約とルール）

安全網のルール（利用者が手元で追従できない IF の変更、取り返しのつかない削除とルールや判断の記録の外し、外部への送信や公開、セキュリティと権限）は、ルールより優先して人に上げます。

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
command = ["algr-agent"]                # Claude Code で動かす（python -m algr.agent と同じ）。省略するとフェイクのエージェント
max_parallel = 2                        # ジョブを同時に走らせる数
[adapters.notify]
use = ".algr/adapters/slack.py:Slack"   # リポジトリに置いたアダプタ
channel = "#dev"

[agents.default]                        # すべての役に効く設定。ジョブの input.agent として実行アダプタに渡す
max_budget_usd = 2
[agents.review]                         # 役ごとの設定が default を上書きする
model = "haiku"

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
結果に `"usage": {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}` を添えると、使ったトークン数としてタスクの `usage` に足し上げ、
`algr stats` の `tokens_per_task`（全項目の合計）と `tokens_per_task_by_kind`（項目ごと）に出します。項目の意味は Claude の API の usage と同じで、
`input_tokens` はキャッシュを除いた分です。キャッシュの 2 項目は省いてかまいません。
結果に `"turns"`（エージェントとの往復の回数）を添えると、`RunStatus.turns` で返し、`events.jsonl` のジョブの終了（`job`）に `turns` として残します。省くと 0 です。
`command` を省略すると、AI を使わず決まりで判断するフェイクのエージェント（`python -m algr.fake_agent`）が走ります。

### k2down

判断ストアの標準は [k2down](https://github.com/yuta4030/k2down) です（`uv tool install git+https://github.com/yuta4030/k2down`）。提案（`propose`）を持つ v0.3 以降が要ります。

```toml
[adapters.decisions]
use = "k2down"
dir = ".algr/decisions"                 # rules.json・proposals.json・usage.jsonl を置くところ（k2down の --dir）
command = ["k2down"]                    # 省略時。uvx で動かすなら ["uvx", "--from", "git+https://github.com/yuta4030/k2down", "k2down"]
```

蒸留エージェントの提案は `k2down propose` で k2down の proposals.json に積み、`algr accept` で `k2down accept`、`algr decline` で `k2down decline` を呼びます。
受け入れも却下も k2down の usage.jsonl に残ります。蒸留に渡す手入れの候補は `k2down report --json` から取ります。k2down の外す提案には理由の項目が無いので、理由は出どころ（`--source`）に入れます。
store を git で管理するなら、k2down のロック（`.k2down.lock`）を `.gitignore` に入れてください。
rules.json の差分を PR にしてマージすることを、人の確認とします。

## 開発

```sh
uv run pytest -q
```

## ライセンス

MIT
