# k2down への要望：提案（追加・外し）の保存（I8）

> **入った（k2down v0.3）。** algr の k2down アダプタは `propose` / `proposals` / `accept` / `decline` に移した（下の「algr のアダプタ側の移し方」）。
> 要望と違うところ：
> - ID はランダム（ルール `R-k7m2q`、提案 `P-a2m4n`）。v0.2 までの連番の ID（`R001`）もそのまま使える
> - コマンドは `propose add|retire|replace` のサブコマンドの形。グループのノードは無くなり、範囲はタグ（`--scope`）で付ける（I2 の分）
> - 外す提案に `--reason` が無い。アダプタは理由を `--source` に入れ、`proposals()` で `reason` に戻して返す。要るなら次の要望にする

R013（ルールや判断の保存と構造は判断ストアの側で持つ）に沿って、いま algr の k2down アダプタが持っている
ルールの提案の保存を、k2down の側で持ってもらう要望の文案。別リポジトリの IF の変更なので、出すかどうかと中身は人が決める。
決めるところは末尾の「人に決めてもらうこと」にまとめた。

## いまの形

- 蒸留エージェントの提案は、アダプタが `.algr/state/proposals.json` に `{"id", "rule", "source"}` で積む。
  I3（`algr/I3`、未マージ）で、外す提案 `{"id", "rule_id", "reason", "source"}` が足される
- `algr accept P001` で、足す提案は `k2down add <rule> --source <source>`、外す提案は `k2down retire <rule_id>` を呼び、ファイルから消す
- `algr decline P001` はファイルから消すだけで、どこにも残らない
- 困っていること
  - 提案の出し入れが k2down の `usage.jsonl` に残らず、却下した提案の履歴が無い（ルールの見直しに使えない）
  - 提案 ID は「残っている提案の最大 + 1」で振るので、受け入れたあとに同じ ID がまた振られる
  - `source` に判断 ID（`I1-4`）と自由記述（`人の方針（2026-10-25）`）が混ざっている。UI は `source` を判断 ID として引いている（R009 に反する）
  - 提案と、それを受け入れたあとのルールが別の場所（`.algr/state/` と `.algr/decisions/`）にある

## 要望

### CLI

既存のコマンド（`add` / `retire` / `replace` / `list` / `apply` ほか）は変えず、次を足す。

```
k2down propose <rule> [--parent N000] [--decision I3-7] [--source <自由記述>]        # 足す提案。提案 ID（P001）を出す
k2down propose --retire R005 --reason <理由> [--decision I3-7] [--source <自由記述>] # 外す提案
k2down propose <rule> --replaces R004 [--decision ...] [--source ...]               # 置き換える提案
k2down proposals [--json]                                                            # まだ決まっていない提案
k2down accept P001                                                                   # 提案を通す。足した（外した）ルールの ID を出す
k2down decline P001 [--reason <理由>]                                                # 提案を却下する
```

- `propose` は冪等にする。同じ種類で同じ中身（足す・置き換えるは文、外すは対象のルール ID）の提案が残っていれば、新しく振らずにその ID を出す
- 提案 ID は `issued` に `P` の採番を足して振り、使い終わった ID を使い回さない
- `accept` は、足すなら `add`、外すなら `retire`、置き換えるなら `replace` と同じことをしてから提案を消す。
  決まっていない提案（無い ID、もう決まった ID）を渡したら、終了コード 1 と stderr で返す
- `--json` の出力は `{"proposals": [...]}`

### 記録の形

まだ決まっていない提案だけを、`rules.json` と同じディレクトリの記録に持つ。決まった提案の履歴は `usage.jsonl` のイベントで持ち、提案の記録には残さない（R011）。

```json
{
  "proposals": [
    {"id": "P001", "kind": "add", "rule": "…", "parent": "N000", "decision": "I3-2", "source": "人の答え"},
    {"id": "P002", "kind": "retire", "rule_id": "R005", "reason": "…", "decision": "I3-7"},
    {"id": "P003", "kind": "replace", "rule_id": "R004", "rule": "…", "decision": "I2-4"}
  ]
}
```

`usage.jsonl` に足すイベント：

```json
{"ts": "…", "event": "proposed", "proposal": "P001", "kind": "add", "rule": "…", "decision": "I3-2"}
{"ts": "…", "event": "accepted", "proposal": "P001", "rule_id": "R017"}
{"ts": "…", "event": "declined", "proposal": "P002", "reason": "…"}
```

`accepted` のあとには、これまでどおり `add` / `retire` / `replace` と同じイベント（`retired`、`replaced` など）も残す。

### ID と出どころの項目の分け方（R009）

- `rule_id`：対象のルール ID（外す・置き換える提案だけ）。ルール ID 以外は入れない
- `decision`：提案のもとになった判断 ID（`I3-2` など。k2down の `--decision` と同じ形）
- `source`：判断 ID で表せない出どころの自由記述（`人の方針（2026-10-25）` など）。ID を入れない
- `accept` で足したルールにも `decision` と `source` を分けて写す。いまのルールの `source` に入っている判断 ID を `decision` に移すかは k2down の側で決めてもらう

## algr のアダプタ側の移し方

k2down が上を出したあとに、algr の別タスクで行う。`DecisionStore` の IF（`propose` / `propose_retire` / `proposals` / `accept` / `reject_proposal`）は変えない。

- `K2down.propose` → `k2down propose <rule> --decision <source>`、`propose_retire` → `k2down propose --retire …`、
  `proposals` → `k2down proposals --json`、`accept` → `k2down accept`、`reject_proposal` → `k2down decline`
- `proposals_file` と、アダプタの設定の `proposals` を外す。`.algr/` の設定の形が変わる
- 手元の `.algr/state/proposals.json` に残っている提案は、移す前に accept か decline で空にする。移行のためのコードは持たない（R008）
- `proposals()` が返す形は、いまの `{"id", "rule", "source"}` から `kind` と `decision` を持つ形に変わる。CLI の出力と UI（`ui.py` の `rule_usage` が `source` を判断 ID として引いている）を `decision` に合わせる
- `tests/fake_k2down.py` に `propose` / `proposals` / `accept` / `decline` を足す

## 併せて出すもの（I2）

I2 のルールの適用範囲で要る分。I2 は人の答え待ちで、出すかは I2 で決める。出すなら同じ要望にまとめる。

- グループのノード（N001 など）を作るコマンド（いまは `add --parent` で既存のノードの下に足せるだけ）
- `list --json` にノード（ID、要約、親）を出す（いまは `rules` だけ）

## 人に決めてもらうこと

1. この形で k2down に要望を出すか（外部への送信。出すのは人）
2. 置き換える提案（`--replaces`）まで入れるか。R015 は「外す・置き換える」を提案にするが、algr にはまだ置き換える提案の入口が無い。入れないなら足すと外すだけにする
3. `source` を `decision` と自由記述に分けるのを、提案だけにするか、既存のルールの記録にも広げるか（保存した記録の形の変更）
4. algr のアダプタの設定から `proposals` を外すこと（`.algr/` の設定の形の変更）
5. I2 の要望と 1 本にまとめるか
