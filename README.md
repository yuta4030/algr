# algr

algr（/ˈælɡər/、アルガー）は、Algorithmic Development という働き方を回すためのツールです。
メタプログラマーが戦略（ルール）を書き、タスクはルールで判断を埋めたうえでマージまで流れます。ルールで決まらない例外だけを、クライアントが答えます。

- **メタの層（アルゴリズミック）**：メタプログラマーが、処理を動かすルールを書き換える
- **処理の層（STP）**：起票 → 実装 → レビュー → マージを、ルールに従って流す。タスクの問いにはクライアントが答える

いまはフェーズ1です。Claude Code（`algr-agent`）と k2down で実タスクを流し、algr 自身も algr で開発しています（[docs/dogfooding.md](docs/dogfooding.md)）。

## 立場

処理の外側に 2 つの立場があります。立場を人が埋めるか、その横の AI が埋めるかは algr は区別しません（`by: "human"` は「ルールではなく立場から来た」と読みます）。
algr が保証するのは「フローの中では決めず、立場に渡す」までです。

| 立場 | やること | 答える判断 | 操作 |
|---|---|---|---|
| クライアント | やりたいことをまとめ、タスクに分けて順番を決める。`new` では何を、何のためにやるか（ルールが当たるかを読める事実）を出す。あとは判断待ちに答え、通せなかった依頼（failed）をやり直すかやめるかを決める。目的を変えるときは、やめて `new` し直す | タスクの問いすべて。要望したものを出してよいか（マージしてよいか、公開 IF を変えてよいか）も含む | `algr new`、`algr answer`、`algr depends`、`algr reopen`、`algr cancel` |
| メタプログラマー | タスクには答えない。クライアントの答えの記録、`algr stats`、report を見てルールを直す | どの判断をルールに任せるか（答えではなく「誰が決めるか」を指す任せるルールも含む） | `algr distill`、`algr accept` / `decline` |

起票・実装・レビューのどの役も、ルールで決まらない判断はクライアントに聞けます。クライアントが答えた判断は蒸留エージェントがルールにし、
メタプログラマーが受け入れたものだけがルールになります。使うほどクライアントに聞く判断は減り、クライアントが答えない状態（Touchless）に近づきます。
こだわり度は `new` の入力にせず、任せるルール（R006、R007 のようなもの）で持ちます。

PR をレビュー・マージするチーム（PJ）は立場の外です。マージ先の手続きとして、マージのアダプタの向こうに置きます。

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
algr list --state waiting                          # 判断待ち（waiting-intake / waiting-implement / waiting-review）をまとめて拾う
algr answer I1 I1-1="OAuth"                        # 答えがそろうと、聞いた役が答えを持って走り直す
algr run                                           # 起票 → 実装 → レビュー → マージ
algr new "ログアウトを足す" --depends I1             # I1 が done になるまで配車しない
algr depends I2 --add I3 --remove I1              # 依存先をあとから足す・外す
algr reopen I2 --note "runner を直した"            # failed のタスクを起票に戻す
algr cancel I3 --note "I4 と I5 に分けた"          # やめる。目的を変えるときも、やめて new し直す
algr distill "日付は UTC で保存する" --topic time  # タスクの外で言った方針を、蒸留エージェントに依頼する
algr proposals                                     # 蒸留エージェントのルール提案（足す提案と外す提案）
algr accept P-a2m4n                                # 提案をルールにする。外す提案ならルールを外す（algr decline で却下）
```

`algr distill` は方針を `source: policy` のタスクに乗せ、蒸留のジョブだけを出します。タスクは最初から done で、起票・実装・レビューには流れず、
イベントも出しません（`algr stats` にも数えません）。蒸留エージェントは既存のルールと突き合わせ、同じ趣旨なら提案せず、
ぶつかるなら新しいルールと、古いルールを外す提案を出します。どちらもメタプログラマーが `algr accept` するまでルールには効きません。

`algr stats` は `.algr/state/events.jsonl` から指標を集計します。実装中とレビューでの差し戻し率、修正依頼率、
STP 率（回答・やり直しが一度も入らずに done になった割合）、タスクあたりの立場の介入回数、判断待ちから答えまでの時間の中央値、
クライアントに聞いた回数（判断待ちの状態ごと。`asked_by_state`）、PJ メンバーの修正依頼の回数（`merge.changes_requested`。algr のレビューが見逃した数。`merge_changes_requested`）、
役ごとのジョブの本数とタスクあたりの本数（`jobs_by_role`・`jobs_per_task`。走り直しがどれだけ減ったか）、
タスクあたりのトークン数（合計と、キャッシュの読み込み・書き込みを分けた項目ごと）を出します。実装中とレビューでの差し戻し率は、それぞれ `implement.asked` と `review.asked` が出たタスクの割合です。
聞いた回数は、どの判断待ち（`waiting-intake` / `waiting-implement` / `waiting-review`）で多いかを見て、どこのルールが足りないかを探すのに使います。
ジョブの本数は方針のタスク（`algr distill`）の蒸留を含みません。集計にはログを使いますが、フローの判定には使いません。`events.jsonl` にはイベントのほか、ジョブの起動（`job_started`。蒸留なら読んだ未決事項の `questions` と report の候補の数の `report` も持つ）と終了（`job`。使ったトークン数の `usage` と往復の回数の `turns`、失敗の理由の `message`、蒸留が出した提案の `proposals` を持つ）、hook の失敗（`hook_error`）、マージのアダプタの失敗（`merge_error`）も時刻付きで残ります。

`algr watch` は常駐して tick を回し続けます（`--interval` 秒ごと）。トリガーの取り込みと ready の配車が止まらないようにするためのプロセスで、
起きたイベントを 1 行ずつ出します。SIGINT（Ctrl-C）か SIGTERM で、いまの tick を終えてから止まります。
保存は楽観ロックなので、`algr answer` などと同時に動かしてかまいません。`merging` のタスクのマージ先も、tick ごとに見ます。

`algr ui` は現状を見るページを `http://127.0.0.1:8765/` で開きます（`--port` で変えられます）。
画面は 2 つの立場で切り替えます。考えることが違うので、上の切り替えで入口を分け、地の色のトーンも変えています
（クライアントは明るめで暖かく、メタプログラマーは一段暗めで冷たい。ライトでもダークでも同じ向き）。

- クライアント（いまの仕事）：あなたに聞きたいこと（判断待ちへの回答と、失敗したタスクのやり直し）、頼んだことの進み具合といま誰の手にあるか、今日届いたもの。3 秒ごとに読み直します。
  ルールや提案は出さず、メタプログラマーの番は件数だけを出します。
- メタプログラマー（ルールを育てる）：前回の見直しからの変化（クライアントに聞いた回数とその判断待ちの状態ごとの内訳、STP 率、介入、差し戻し、PJ の修正依頼を前の期間と並べる）、
  失敗（原因を見る。やり直すかはクライアント）、決めること（提案を 1 件ずつ、根拠になったクライアントの答えや report と一緒に）、ルールの効き、まだルールでない論点、
  蒸留エージェントの動き（読んだクライアントの答えと report の候補、出した提案、失敗）。サブページに推移（起票した週ごとの差し戻し率・介入回数・STP 率・ジョブ / 件など）、
  フロー（遷移ごとの通過回数と状態ごとの滞在時間）、ジョブ（タスクごとのジョブの時間とトークン数）があります。
  前回の見直しの時刻は「見直しを終える」でブラウザに覚え（無ければ直近 7 日）、`/api/review?since=` に渡します。コアは見直しの状態を持ちません。

ヘルプは algr 自身の説明です。大きく変わらないもの（コンセプト、立場、ID）は `ui.html` に書き、状態・遷移・不変条件・役ごとの結果の形・プロンプト・コマンドのリファレンスはコードから組み立てます（`ui.help_page`）。
図（2 つの層、状態とイベント、ルールを育てる輪）は `src/algr/diagrams.py` に箱と矢印のデータで持ち、SVG に書き出します。人・AI・データで形と色を分けています。状態とイベントの図はテストで `TRANSITIONS` と突き合わせます。
説明の文はコードの横（`flow.STATE_HELP` / `EVENT_HELP`、`agents.ROLE_HELP`、`cli.STANCES`）に 1 か所だけ置き、このファイルの表はテスト（`tests/test_help.py`）でそれと突き合わせます。
読むのはアダプタの IF とイベントログだけで、GUI は判断も状態も持ちません。いまは閲覧だけで、操作のボタンには同じことをする CLI を添えています。

`algr tick` は 1 周だけ回します（トリガーを取り込み、ジョブの結果を受け取り、merging のマージ先を見て、ready を配車し、要るジョブを起動する）。

`--json` を付けると JSON を返します。エラー時は終了コード 1 で、stderr に `error: ...` を書きます。

## フロー

タスクは 11 の状態を移り、遷移ごとにイベントが 1 つ出ます。イベントがそのまま hook の差し込み口です。

```text
主経路       intake ─▶ ready ─▶ in-progress ─▶ review ─▶ merging ─▶ done
聞く         intake ─▶ waiting-intake ─▶ intake
             in-progress ─▶ waiting-implement ─▶ in-progress
             review ─▶ waiting-review ─▶ review（答えを持ったレビューが承認か修正依頼かを決める）
修正依頼     review ─▶ in-progress（flow.max_rework 回まで。超えたら failed）
PJ の指摘    merging ─▶ in-progress（max_rework には数えない）
失敗         intake / in-progress / review / merging ─▶ failed ─▶ intake（クライアントが reopen する）
やめる       done と canceled 以外 ─▶ canceled（クライアントが cancel する）
```

立場に渡るのは、判断待ちの 3 つ（waiting-*）と failed だけです。起票・実装・レビューのどの役も、ルールで決まらない判断はクライアントに聞きます。
聞いた役は問いを結果に書いて終わり（返事を待たない）、答えがそろうと聞いた役の状態に戻って、その役のジョブが要約から起動し直されます。起票には戻しません。
答えは一部ずつでもよく、残りがあるあいだは判断待ちのままです。戻り先はタスクの項目ではなく状態で持つので、状態ごとの滞在時間や通過回数がそのまま役ごとに分かれます。
クライアントに聞くときは必ず通知します。

レビューが通ると、マージのアダプタにマージを頼み（`request`）、`merging` でマージ先に入るのを待ちます。tick ごとに `status` を見て、
`merged` なら `done`、`changes` なら PJ の指摘を `feedback` にして実装に戻し、`closed`（マージされずに閉じた）なら `failed`（`role` は `merge`）にします。
マージの前に止めたいときは、ルール（例：「マージ前に必ずクライアントに聞く」）に当たったレビューが `waiting-review` でクライアントに聞きます。
依存はマージ先に入ったとき（`done`）に解けます。
failed は、クライアントの依頼を通せなかったことを表します。やり直す（`algr reopen`）かは依頼したクライアントが決めます。原因がフロー側（実行環境、プロンプト、エージェント）にあれば、先にメタプログラマーが直します。
failed になった理由（`role` と `message`）はタスクの `failure` に残り、`algr show` で読めます。reopen すると消えます。

クライアントは done になっていないタスクを `algr cancel` でやめられます。目的を変える操作は持たず、やめて `new` し直します。
やめると走っているジョブを止め（実行アダプタが `stop` を持つとき）、あとから届いた結果は捨てます。マージを頼んだ PR は閉じません（マージのアダプタに取り消しの口は無い）。
依存しているタスクは待ち続けるので、依存を外すか一緒にやめるかを決めるよう知らせます。やめたタスクの答えは蒸留しません（蒸留は done のあと）。
エージェントが落ちたときの `message` には、エージェントの出力（`algr.agent` なら claude の `result` や `is_error`）の最後の方が入ります。

イベント名とペイロード（`Event.data`）の形は固定です（`src/algr/flow.py` の `TRANSITIONS` と `EVENT_DATA`）。
どのイベントにも遷移元の状態 `from` が付き（`task.created` は `""`）、決断 ID の並びは必ず `questions` で渡します。

| イベント | ペイロード | 既定の処理 |
|---|---|---|
| `task.created` | `source`（`human` / `auto`） | 起票エージェントを起動する |
| `intake.asked` | `channel`（`inline` / `queue`）、`questions` | クライアントに知らせる（channel は聞き方の違いだけ） |
| `intake.answered` | `questions`（クライアントが答えたもの） | 起票エージェントを起動し直す |
| `task.ready` | — | 配車を待つ |
| `task.started` | — | 実装エージェントを起動する |
| `implement.asked` | `channel`、`questions` | クライアントに知らせる |
| `implement.answered` | `questions` | 実装エージェントを起動し直す（前の PR と答えを渡す） |
| `task.submitted` | `pr` | レビューエージェントを起動する |
| `review.changes_requested` | `changes`（修正依頼） | 実装エージェントを起動する（前の PR と指摘を渡す） |
| `review.asked` | `channel`、`questions`、`reasons`（決断 ID → 異議） | 異議のある判断と新しい問いを未決事項にし、クライアントに知らせる |
| `review.answered` | `questions` | レビューエージェントを起動し直す（答えを持って承認か修正依頼かを決める） |
| `review.approved` | `pr` | マージのアダプタにマージを頼む（`request`） |
| `merge.changes_requested` | `changes`（PJ の指摘） | PJ の指摘を修正依頼として、実装エージェントを起動する |
| `task.done` | `pr` | ルールに従った判断を hit として記録し、クライアントが答えた未決事項があれば蒸留エージェントを起動する |
| `task.failed` | `role`、`message` | クライアントに知らせる |
| `task.reopened` | `note` | 起票エージェントを起動する |
| `task.canceled` | `note` | 走っているジョブを止め、依存しているタスクがあれば知らせる |

イベント名は、聞く・答えるものを役ごとに `<役>.asked` / `<役>.answered` にしています（判断待ちを役ごとの状態に分けたので、どの役が聞いたかがイベントでも分かるように）。
クライアントが答えた未決事項は立場が決めたことなので、再レビューで異議が出ても蒸し返しません。レビューの異議から立てた問いには、もとの判断が `question_ids` で従います。

トリガーが外から送れるのは、立場の操作と同じ `task.created` / `task.answered` / `task.reopened` / `task.canceled` だけです。
回答はどの役が聞いたかを知らなくてよいよう `task.answered`（`data` は `{"answers": {"<決断 ID>": "<答え>"}}`）で送り、エンジンが判断待ちの状態から `<役>.answered` を起こします。

### hook

hook は `.algr/config.toml` の `[hooks]` に、イベント名（すべてのイベントを受けるなら `"*"`）ごとに並べます。

- 形は `hook(event, engine)`。`event.data` は上の表の形です。タスクは `engine.tasks.get(event.task_id)` で読み直します
- 呼ばれるのは状態を保存し、既定の処理を済ませたあとです。hook は遷移を止められません（止める判断はエージェントと不変条件が持ちます）
- hook の中で `engine.emit` したイベントは、今のイベントの hook がすべて済んでから処理します
- hook が例外を投げてもフローは止めません。`.algr/state/events.jsonl` に `hook_error` を残し、通知アダプタで知らせます
- 知らないイベント名の hook は、設定の読み込みでエラーにします
- イベントは再送されることがあるので、hook もアダプタと同じく冪等に書きます

### 不変条件とタスクの持ち物

遷移ごとに不変条件があります（例：ready にするには未決事項がゼロ、ルールで埋めた未決事項にはルールの ID が付いている）。

タスクは 2 種類の判断を別々に持ちます。どちらも決断 ID（`I1-1`）をタスク内で通し番号で振ります。

- **未決事項**（`questions`）：起票で埋めるもの（実装とレビューが聞いたものも含む）。ルールで埋まるか、クライアントが答える
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
| `implement` | in-progress にいる間 | `{"pr", "decisions": [{"text", "answer", "topic"?, "rule_ids"?, "question_ids"?}]}`、聞くときは `{"questions": [{"text", "topic"?, "options"?, "recommended"?}]}` |
| `review` | review にいる間 | `{"approved": bool, "objections": {"<決断 ID>": "<理由>"}, "changes"?: ["<修正依頼>"], "questions"?: [{"text", "topic"?, "options"?, "recommended"?}]}` |
| `distill` | done になったタスクに、クライアントが答えた未決事項がある時（1 タスク 1 回。`algr distill` の方針はすぐ） | `{"rules": [{"rule", "source"?}], "retire"?: [{"rule_id", "reason", "source"?}]}` |

`intake` と `implement` の結果には、見つけた別件を起票する `"tasks": [{"title", "purpose"?, "questions"?, "depends"?, "priority"?}]` を付けられます。
別件は `source: auto` で起票され、`depends` には既にあるタスク（このタスクを含む）の ID を書きます。

実装とレビューの `questions` は、ルールで決まらない判断の問いで、クライアントに聞きます（聞く前に `input.rules` を当て、当たらないものだけを聞きます）。
`objections` は判断への異議で、これもクライアントに聞きます。`changes` はテスト失敗やバグなど判断以外の指摘で、実装に戻します。
レビューが聞いたときは、答えがそろってから走り直したレビューが、承認か修正依頼かを決めます。答えが判断どおりかをコアは見比べません。
ジョブの入力（`input`）には今のルールが入り、実装には前の回の PR と判断（`previous`）と修正依頼（`feedback`）、
レビューにはクライアントが決めた未決事項の ID（`settled`）、蒸留には判断ストアの手入れの候補（`report`）も入ります。やり直しは一から作らず、差分だけ直す前提です。

蒸留はクライアントの答えのほか、`report` の落とす候補（一度も当たらないルール）・書き直す候補（外れや異議の多いルール）・ぶつかる組から、
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

安全網のルール（利用者が手元で追従できない IF の変更、取り返しのつかない削除とルールや判断の記録の外し、外部への送信や公開、セキュリティと権限）は、ルールより優先してクライアントに聞きます。

## 設定とアダプタ

リポジトリの `.algr/config.toml` に、個人の `~/.algr/config.toml`（`ALGR_HOME` で変更可）を重ねます。個人の設定が勝ちます。

```toml
[flow]
max_parallel = 1                        # 同時に in-progress にするタスクの数
max_rework = 2                          # レビューの修正依頼で実装に戻す回数の上限（PJ の修正依頼は数えない）

[adapters.task]
use = "fake.tasks"                      # 組み込み名
[adapters.executor]
use = "fake.process"
command = ["algr-agent"]                # Claude Code で動かす（python -m algr.agent と同じ）。省略するとフェイクのエージェント
max_parallel = 2                        # ジョブを同時に走らせる数
[adapters.merge]
use = ".algr/adapters/github.py:PullRequest"  # マージ先（main か統合ブランチか）はアダプタの設定で選ぶ
into = "main"
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
| `merge` | 承認した PR をマージ先に入れる（`request(task)` / `status(task)`） | `fake.merge`：頼まれたらすぐ `merged` を返す |
| `notify` | 判断待ちと失敗を立場に届ける | `stub`：ファイルに追記 |

`fake.process` のコマンドには `ALGR_JOB_ID` / `ALGR_ROLE` / `ALGR_JOB_FILE` / `ALGR_RESULT_FILE` が渡ります。
`ALGR_JOB_FILE` には `{"id", "role", "task", "input"}` が入っていて、コマンドは役ごとの結果を `ALGR_RESULT_FILE` に書いて終了します。
結果に `"usage": {"input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}` を添えると、使ったトークン数としてタスクの `usage` に足し上げ、
`algr stats` の `tokens_per_task`（全項目の合計）と `tokens_per_task_by_kind`（項目ごと）に出します。項目の意味は Claude の API の usage と同じで、
`input_tokens` はキャッシュを除いた分です。キャッシュの 2 項目は省いてかまいません。
結果に `"turns"`（エージェントとの往復の回数）を添えると、`RunStatus.turns` で返し、`events.jsonl` のジョブの終了（`job`）に `turns` として残します。省くと 0 です。
`command` を省略すると、AI を使わず決まりで判断するフェイクのエージェント（`python -m algr.fake_agent`）が走ります。

### マージ

`merge` のアダプタ（`MergeAdapter`）は、実行アダプタと同じ組み立てです。`request(task)` は `task.pr` のマージを頼み、同じタスクの同じ PR で二度呼ばれても二重に頼みません
（コアは `merging` にいる間、tick ごとに呼びます）。`status(task)` は `MergeStatus` を返し、`state` は `merged` / `open` / `changes`（`changes` に PJ の指摘）/ `closed` のどれかです。
`changes` は、いまの PR を頼んだあとに来た指摘だけを返します。アダプタが `AdapterError` を投げたら、次の tick でやり直します。

マージ先は利用者がアダプタの設定で選びます。コアは同じです。

- **main に入れる**：PJ が 1 件ずつレビューしてマージし、アダプタは PR の状態を見る。依存の鎖の 1 本ごとに PJ を待つので、タスクが大きくなりがち
- **統合ブランチに入れる**：アダプタがすぐ git でマージし、PJ は統合ブランチの PR をまとめて見る。タスクを小さく保てる。統合 PR への PJ の指摘は新しいタスクとして起票する。
  どのタスクを 1 つの統合 PR にまとめるかは、クライアントがタスクに分けるときに決める（コアはまとまりを持たない）

タスク管理とコードの置き場は別のシステムになりうるので、`task` とは別の種類にしています。同じシステムなら、1 つのクラスで両方の IF を実装し、設定の 2 か所から指します。

### k2down

判断ストアの標準は [k2down](https://github.com/yuta4030/k2down) です（`uv tool install git+https://github.com/yuta4030/k2down`、版を固定するなら末尾に `@v0.4.0`）。提案の理由（`propose --reason`）を持つ v0.4 以降が要ります。algr は最初に k2down を呼ぶときに `k2down --version` で確かめ、古ければエラーにします。

```toml
[adapters.decisions]
use = "k2down"
dir = ".algr/decisions"                 # rules.json・proposals.json・usage.jsonl を置くところ（k2down の --dir）
command = ["k2down"]                    # 省略時。uvx で動かすなら ["uvx", "--from", "git+https://github.com/yuta4030/k2down", "k2down"]
```

蒸留エージェントの提案は `k2down propose` で k2down の proposals.json に積み、`algr accept` で `k2down accept`、`algr decline` で `k2down decline` を呼びます。
受け入れも却下も k2down の usage.jsonl に残ります。蒸留に渡す手入れの候補は `k2down report --json` から取ります。外す提案の理由は `--reason`、元になった決断 ID は `--decision` で渡します。
k2down は README の「互換性」で `--json` の出力と終了コードを約束し、0.x のうちは壊れる変更でマイナーを上げます。アダプタは知らないキーを無視します。
store を git で管理するなら、k2down のロック（`.k2down.lock`）を `.gitignore` に入れてください。
rules.json の差分は PR に載ります。

## 開発

```sh
uv run pytest -q
```

## ライセンス

MIT
