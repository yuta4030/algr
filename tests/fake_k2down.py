"""テスト用の k2down の CLI。algr の K2down アダプタが使う部分だけを真似て、呼ばれた引数を calls.jsonl に残す。"""

import json
import sys
from pathlib import Path

argv = sys.argv[1:]
assert argv[0] == "--dir"
store = Path(argv[1])
command, args = argv[2], argv[3:]
store.mkdir(parents=True, exist_ok=True)
with (store / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps([command, *args]) + "\n")

rules_file = store / "rules.json"
rules = json.loads(rules_file.read_text(encoding="utf-8")) if rules_file.exists() else []
if command == "list":
    print(json.dumps({"rules": [{"id": "N000", "rule": "全体", "parent": None}, *rules]}))
elif command == "add":
    rule_id = f"R{len(rules) + 1:03d}"
    rules.append({"id": rule_id, "rule": args[0], "parent": "N000"})
    rules_file.write_text(json.dumps(rules), encoding="utf-8")
    print(rule_id)
elif command == "apply" and "R404" in args:
    print("error: unknown rule: R404", file=sys.stderr)
    sys.exit(1)
