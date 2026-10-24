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

## 技術スタック
- Python 3.11+、パッケージ管理は uv。k2down に揃える
- 本体は標準ライブラリのみ（依存ゼロ）。設定は TOML（`tomllib`）
- CLI は argparse。既定の出力はプレーンテキスト、`--json` で JSON。エラーは終了コード 1 と stderr
- テストは pytest。外部には一切つながず、フェイクアダプタで回す

## 状態とイベント
- 状態：intake / needs-decision / ready / in-progress / review / done / failed
- イベントと遷移は `src/algr/flow.py` の `TRANSITIONS`、不変条件は `INVARIANTS`。イベント名を変えるときは README の表も直す
- 決断 ID はタスク単位で振る（`I123-1`）。k2down の `--decision` にそのまま渡す
- 未決事項（`Task.questions`、起票で埋める）と判断（`Task.decisions`、実装エージェントが PR に載せる）は混ぜない

## エージェント
- 起票・実装・レビュー・蒸留のエージェントは、すべて実行アダプタがジョブとして起動する。コアの中で同期に呼ばない
- コアはタスクの状態から要るジョブを出して起動し、結果を受け取って状態を動かす。役ごとの結果の形は `src/algr/agents.py`
- エンジンのハンドラはタスクを読み直してから書き換える。古いタスクのまま保存すると、ほかの処理の変更を消す

## フェーズ0（いま）の範囲
- algr 本体の CLI、`.algr/` と `~/.algr/` の設定の読み込み
- 5 つのアダプタ IF（通知はスタブ）と、フェイクアダプタ（ファイルのタスク、ローカルプロセスの実行、ファイルの判断ストア、inbox トリガー）
- エージェントは結果の形とフェイク（`src/algr/fake_agent.py`、AI を使わず決まりで判断する）だけ
- 完了条件：フェイクアダプタでループが一周するテスト（`tests/test_loop.py`）が通る

## フェーズ1で作るもの（まだ作らない）
- 同期の起票（対話）、k2down の判断ストアアダプタ、GitOps ボードと herdr のアダプタ、ready を監視する常駐プロセス
- 実装・レビュー・蒸留のエージェントのプロンプトとスキル、安全網のルール

## ディレクトリ構成
```
src/algr/
  cli.py        # CLI
  config.py     # 設定の読み込みとアダプタの解決
  engine.py     # イベントを起こし、既定の処理と hook を呼ぶ
  flow.py       # 状態遷移と不変条件
  model.py      # Task / Question / Decision / Job / Event
  agents.py     # エージェントの役ごとの入力と結果の形
  fake_agent.py # フェイクのエージェント（python -m algr.fake_agent）
  adapters/     # アダプタ IF（__init__.py）とフェイク（fake.py）
tests/
```
