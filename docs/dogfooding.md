# algr を algr で開発する

algr 自身の開発を algr で回す。起票・実装・レビュー・蒸留はエージェントが行う。クライアントは判断待ちへの回答、失敗したタスクのやり直し（`algr reopen`）、統合ブランチへのマージを、メタプログラマーはルールの受け入れを行う。

## 置いているもの

| パス | 中身 | git |
|---|---|---|
| `.algr/config.toml` | 設定（実行は `fake.process` で `algr-agent`、判断ストアは k2down、マージは `.algr/adapters/branch.py`） | 入れる |
| `.algr/adapters/branch.py` | マージのアダプタ。`algr/<ID>` が統合ブランチに入ったら merged を返す（マージはクライアントが手で行う） | 入れる |
| `.algr/prompts/` | 実装とレビューのプロンプトへの追記（ブランチ、テスト、リンタ） | 入れる |
| `.algr/decisions/` | ルール（`rules.json`）、まだ決まっていない提案（`proposals.json`）、判断の記録（`usage.jsonl`） | 入れる |
| `.algr/tasks/`、`.algr/state/` | タスク、イベントの記録、ジョブの出力 | 入れない |

ルールの追加や言い直しは PR の差分で見える。タスクと記録は残らないので、タスクの番号は環境を作るたびに I1 から振り直しになる。

## 用意

claude.ai/code のクラウドでも手元でも動く。クラウドでは `claude -p` がサブスクの OAuth で動き、API キーは要らない。

```sh
uv tool install git+https://github.com/yuta4030/k2down
# 動かす algr は作業ツリーと別に入れる。エージェントの書きかけのコードを watch が読まないように
git worktree add --detach ../algr-tool origin/main && uv tool install --force ../algr-tool && git worktree remove --force ../algr-tool
```

マージした変更を watch に効かせるには、同じように入れ直して watch を再起動する。

## 回し方

1. 統合ブランチ（PR で main に入れるブランチ）をチェックアウトする。エージェントはそこから `algr/<ID>` を切り、終わるとそこに戻る
2. `algr watch --interval 3` を常駐させる
3. `algr new "<題>" --purpose "<目的>" -q "<論点>: <問い>"` で起票する
4. `intake.asked` / `implement.asked` / `review.asked` が出たら（まとめて拾うなら `algr list --state waiting`）、`algr show <ID>` で問いを見て答える（`algr answer`）。推奨案はそのまま受けず、コードを読んで確かめる（下の「分かったこと」）
5. `review.approved` が出たらタスクは `merging` で待つ。`git diff HEAD...algr/<ID>` と、`git worktree` に出したテストで確かめる
6. マージするなら `git merge --ff-only algr/<ID>`。次の tick でアダプタが見つけて done にする。`algr/<ID>` は done になってから消す（先に消すと closed として failed になる）。
   統合ブランチを push して PR にする。PR の説明には、クライアントの答えとエージェントの判断を載せる。PR への指摘は新しいタスクとして起票する
7. 蒸留の提案（`algr proposals`）を受け入れるなら `algr accept`。文が曖昧なら `k2down --dir .algr/decisions replace` で言い直す。ルールの変更も同じ PR に入れる

エージェントが作業ツリーを書き換えている間は、人の側で作業ツリーを触らない（未コミットの変更はエージェントのもの）。

## 次にやること

1. レビューが人に聞かずに追認できる小さな判断の線を決める（UI の分は R006 で一部カバー）
2. トークンを減らす。人が答えるたびに起票が走り直す（蒸留はタスクの done で 1 回にした）
3. 2 件目のレポート（[ローカル試行レポート](https://claude.ai/artifact/TzZmvKswAYqvPoysiQWuP8)の続き）

## 基準（2026-10-27、フロー・プロンプト・指標の作り直し（v2）を入れた時点）

ここから先は、この値と比べて v2 のプロンプトが効いたかを見る。比べるのは、走り直し（ジョブ / 件、役ごとのトークン）と、判断待ちの状態ごとの聞いた回数。

`algr stats`：タスク 14（done 9、failed の記録 3）、STP 33.3%、介入 1.29 回/件、判断待ちの中央値 2,055 秒、トークン 189 万/件、ジョブ 5.36 本/件。
聞いた回数は waiting-intake 15・waiting-implement 2・waiting-review 1。差し戻し率は実装中 16.7%・レビュー 9.1%、修正依頼率 0%、PJ の修正依頼 0。
役ごとのジョブは起票 34・実装 15・レビュー 12・蒸留 14、トークンは起票 440 万・実装 1,601 万・レビュー 553 万・蒸留 53 万。

v2 より前の記録は旧いイベント名だったので、v2 の名前に書き換えてから数えた（`decision.requested` は `intake.asked`、`task.returned` と続く `decision.requested` は 1 つの `implement.asked`、
`review.escalated` は `review.asked`、`decision.resolved` は直前に聞いた役の `*.answered`、`from` の `approved` は `merging`）。
旧フローは答えるたびに起票から走り直したので、ジョブ数とトークンは v2 より多めに出る。聞いた回数は前後で同じ物差しで比べられる。

## 分かったこと（2026-10-24、最初の 2 件）

最初の 2 件は PR #7 にまとめた。I1 は承認をイベント `review.approved` にして watch に出し、I2 は `Task.approved` をやめて状態 `approved` を足した。

`algr stats`：done 2、STP 0%、介入 3 回/件、レビューの差し戻し率 50%、判断待ちの中央値 444 秒、トークン 194 万/件（ジョブ 16 本）。
`k2down report`：判断 14 件のうちルールが当たったのは 3 件（R001 が 1、R002 が 2、外れ 0）。

効いたこと

- 人がコードを書かずに、起票からマージまで 2 件流れた。CI は Linux・Mac・Windows で通った
- 1 件目の答えから作ったルールが、2 件目で当たった。I2 の判断の大半にルール ID が付いた
- I1 で足した `review.approved` が、I2 の承認のときに watch に出た

効いていないこと

- 推奨案をそのまま受けると、設計が悪い方へ行く場面が 3 回あった
  - マージ待ちを「マージまで繰り返し出す」：watch の出力が埋まる
  - 承認済みを「events.jsonl から導く」：省略できるログに状態を頼る
  - 存在しないイベント（rework、pr.updated）を前提にした問い：起票がコードを見ていない
- 実装が人の答え（「フラグは要らない」）に反してフラグを残し、根拠なしの判断として通した。レビューも見逃した
- 逆にレビューは、UI の見せ方という小さな判断を人に回した（聞きすぎ）
- 実装エージェントが別件を自分で起票したのは良い動きだったが、その起票の前提（イベントから導く）が間違っていた
