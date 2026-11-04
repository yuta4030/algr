# CHANGELOG

algr の版ごとの変更です。
0.1.0 以降の各版には `v<版>` のタグがあり、`uv tool install git+https://github.com/yuta4030/algr@v0.1.0` のように固定して入れられます。

## [0.1.0] - 2026-11-04

最初の版。

- フローのコア：状態とイベント、遷移ごとの不変条件、判断待ちを役ごとの状態に持つ
- アダプタ IF（タスク、トリガー、実行、判断ストア、通知、マージ）とフェイクアダプタ
- 起票・実装・レビュー・蒸留のエージェント（実行アダプタのジョブ、`algr-agent`）と安全網のルール
- 判断ストアに k2down（v0.4 以降）
- 常駐プロセス（`algr watch`）、指標（`algr stats`）、画面（`algr ui`）
- リリースの自動化。main の版にタグがなければ、CI が `v<版>` のタグと GitHub Release を作る（`.github/workflows/release.yml`）
