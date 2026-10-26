# CLAUDE.md — algr

algr は Algorithmic Development を回すためのフロー。ルールで判断を埋め、タスクを人の手に触れずにマージまで流す。
企画書（コンセプト・戦略・戦術）は https://claude.ai/artifact/JpCoJPm1kdmhNiFFtZShYG 。このファイルは実装時に守ることをまとめたもの。

## 絶対に守る制約
- **提供するのはフローだけ。** コアが持つのは状態遷移とイベント、遷移ごとの不変条件、エージェントのプロンプトとスキル
- **コアはタスク管理・実行場所・判断の保存を持たない。** 外部との接点はすべてアダプタ（`src/algr/adapters/__init__.py` の IF）を通す
- コアは判断しない。判断はエージェント（ホスト側の AI）が行い、コアは結果で状態を動かして記録する
- 判断には根拠を付ける。ルールで決めた判断には必ずルール ID を残し、根拠がなければ人に上げる
- 取り返しのつかないもの（公開 IF の変更、削除、外部送信）は人のゲートを通す
- 社内固有のものは利用側の `.algr/` に置き、algr 本体には持ち込まない
- アダプタは冪等に作る。イベントを再送しても壊れないこと

## 人の立場
- 人は処理の外側で 2 つの立場に立つ。表は README の「人の役割」
- クライアント：何をやるか・目的・こだわり度を出す。「何を大事にするか」が問われる判断に答える（`new`、`answer`）
- メタプログラマー：何を自動で通してよいかを決める。ルールの受け入れ、マージのゲート、公開 IF の境界を持つ。「どこまで任せてよいか」が問われる判断に答える（`accept`、`merge`、`reopen`）
- 人に聞く判断は起票に寄せる。人の操作を足すときは、どちらの立場の操作かを決めてから足す

## 技術スタック
- Python 3.11+、パッケージ管理は uv。k2down に揃える
- 本体は標準ライブラリのみ（依存ゼロ）。設定は TOML（`tomllib`）
- CLI は argparse。既定の出力はプレーンテキスト、`--json` で JSON。エラーは終了コード 1 と stderr
- テストは pytest。外部には一切つながず、フェイクアダプタで回す

## 状態とイベント
- 状態：intake / needs-decision / ready / in-progress / review / approved / done / failed
- イベントと遷移は `src/algr/flow.py` の `TRANSITIONS`、不変条件は `INVARIANTS`、ペイロードの形は `EVENT_DATA`。イベント名やペイロードを変えるときは README の表も直す
- 状態・イベント・役・人の操作の説明はコードの横に 1 か所だけ置く（`flow.STATE_HELP` / `EVENT_HELP`、`agents.ROLE_HELP`、`cli.STANCES`）。`algr ui` のヘルプはそこから組み立て、README の表は `tests/test_help.py` が突き合わせる
- ヘルプの図は `src/algr/diagrams.py` のデータ（座標は手で置く）。状態やイベントを足したら状態とイベントの図にも箱と矢印を足す（テストが落ちる）。直したら `algr ui` で見た目を確かめる
- hook は既定の処理のあとに呼ぶ観察者で、遷移を止めない。hook の失敗でフローを止めない
- 決断 ID はタスク単位で振る（`I123-1`）。k2down の `--decision` にそのまま渡す
- 未決事項（`Task.questions`、起票で埋める）と判断（`Task.decisions`、実装エージェントが PR に載せる）は混ぜない

## エージェント
- 起票・実装・レビュー・蒸留のエージェントは、すべて実行アダプタがジョブとして起動する。コアの中で同期に呼ばない
- コアはタスクの状態から要るジョブを出して起動し、結果を受け取って状態を動かす。役ごとの結果の形は `src/algr/agents.py`
- エンジンのハンドラはタスクを読み直してから書き換える。古いタスクのまま保存すると、ほかの処理の変更を消す

## フェーズ0（済み）
- algr 本体の CLI、`.algr/` と `~/.algr/` の設定の読み込み
- 5 つのアダプタ IF（通知はスタブ）と、フェイクアダプタ（ファイルのタスク、ローカルプロセスの実行、ファイルの判断ストア、inbox トリガー）
- 完了条件：フェイクアダプタでループが一周するテスト（`tests/test_loop.py`）が通る

## フェーズ1（いま）
- 済み：差し戻し・修正依頼・やり直し・マージのゲート、タスクの依存、楽観ロック、k2down の判断ストアアダプタ、
  常駐プロセス（`algr watch`）、エージェントのプロンプトと安全網のルール（`src/algr/prompts/`、`python -m algr.agent`）、指標（`algr stats`）
- 残り：同期の起票（対話）、GitOps ボードと herdr のアダプタ、既存の作業記録からの初期ルール。決めることは `docs/open-questions.md`
- 完了条件：実タスクが起票とルールのレビューだけでマージまで流れ、差し戻し率が下がる傾向を `algr stats` で示す
- algr 自身も algr で開発している。設定とルールは `.algr/`、回し方・次にやること・分かったことは `docs/dogfooding.md`

## ディレクトリ構成
```
src/algr/
  cli.py        # CLI
  config.py     # 設定の読み込みとアダプタの解決
  engine.py     # イベントを起こし、既定の処理と hook を呼ぶ
  flow.py       # 状態遷移と不変条件
  model.py      # Task / Question / Decision / Job / Event
  agents.py     # エージェントの役ごとの入力と結果の形
  agent.py      # Claude Code でエージェントを走らせるコマンド（python -m algr.agent）
  prompts/      # 役ごとのプロンプトと安全網のルール（利用側は .algr/prompts/ に追記する）
  fake_agent.py # フェイクのエージェント（python -m algr.fake_agent）
  stats.py      # 指標の集計（events.jsonl から）
  ui.py         # 現状を見る GUI（algr ui）。HTML は ui.html。判断も状態も持たず、操作は Engine のメソッドだけを通す
  diagrams.py   # ヘルプの図（箱と矢印のデータと、SVG への書き出し）
  adapters/     # アダプタ IF（__init__.py）、フェイク（fake.py）、k2down（k2down.py）
tests/
```
