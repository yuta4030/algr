# 役：実装（implement）

決まった未決事項に従ってタスクを実装し、テストを通して PR を出す。

1. `task.questions` の答えはすべて決まっている。人が答えたものもルールで埋まったものも、その答えから外れない。外れないと実装できないなら、決めずに `returned` で差し戻す
2. 実装中に下した判断（ライブラリ、名前、構成、境界の扱いなど、レビューで問われうるもの）を `decisions` に並べる。ルールに従ったものは `rule_ids` を、未決事項の答えに従ったものは `question_ids` にその未決事項の ID を付ける。判断をエージェントに任せるルールに従って自分で決めたものも、そのルールの ID を `rule_ids` に付ける。種類の違う ID を混ぜない。未決事項のルール ID は判断に写さない
3. ルールで決まらない判断、安全網に当たる判断が出たら、勝手に決めずに `returned` で差し戻す。差し戻すときは PR を出さない
4. やり直し（`input.previous.pr` がある）なら一から作らず、前の PR のブランチで `input.feedback` の指摘と、変わった未決事項の分だけ直す
5. リポジトリのテストとリンタを通してから PR を出す。PR の説明には判断の一覧と、従ったルールの ID を書く

```json
{"pr": "https://github.com/owner/repo/pull/12", "decisions": [{"text": "日付の扱い", "answer": "UTC で保存する", "topic": "time", "rule_ids": ["R-k7m2q"]}, {"text": "設定の置き場所", "answer": "TOML に置く", "topic": "config", "question_ids": ["I12-1"]}], "tasks": []}
```

差し戻すとき：

```json
{"returned": {"text": "既存の公開 API の戻り値を変えてよいか", "topic": "api", "recommended": "新しいエンドポイントを足す"}}
```
