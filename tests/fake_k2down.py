"""テスト用の k2down の CLI。algr の K2down アダプタが使う部分だけを真似て、呼ばれた引数を calls.jsonl に残す。"""

import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv == ["--version"]:
    print(os.environ.get("FAKE_K2DOWN_VERSION", "k2down 0.3.0"))
    sys.exit(0)
assert argv[0] == "--dir"
store = Path(argv[1])
command, args = argv[2], argv[3:]
store.mkdir(parents=True, exist_ok=True)
with (store / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps([command, *args]) + "\n")

rules_file = store / "rules.json"
rules = json.loads(rules_file.read_text(encoding="utf-8")) if rules_file.exists() else []
proposals_file = store / "proposals.json"
proposals = json.loads(proposals_file.read_text(encoding="utf-8")) if proposals_file.exists() else {"issued": 0, "proposals": []}


def option(name):
    return args[args.index(name) + 1] if name in args else None


def add_rule(text):
    rule_id = f"R{len(rules) + 1:03d}"
    rules.append({"id": rule_id, "rule": text, "parent": "N000"})
    rules_file.write_text(json.dumps(rules), encoding="utf-8")
    return rule_id


def pending(proposal_id):
    proposal = next((p for p in proposals["proposals"] if p["id"] == proposal_id), None)
    if proposal is None:
        print(f"error: unknown proposal: {proposal_id}", file=sys.stderr)
        sys.exit(1)
    return proposal


if command == "list":
    print(json.dumps({"rules": [{"id": "N000", "rule": "全体", "parent": None}, *rules]}))
elif command == "add":
    print(add_rule(args[0]))
elif command == "retire":
    rules_file.write_text(json.dumps([r for r in rules if r["id"] != args[0]]), encoding="utf-8")
elif command == "apply" and "R404" in args:
    print("error: unknown rule: R404", file=sys.stderr)
    sys.exit(1)
elif command == "propose":
    kind, target = args[0], args[1]
    proposal = {"kind": kind, ("rule" if kind == "add" else "rule_id"): target}
    if option("--decision") is not None:
        proposal["decision"] = option("--decision")
    if option("--source") is not None:
        proposal["source"] = option("--source")
    same = next((p for p in proposals["proposals"] if p["kind"] == kind and p.get("rule", p.get("rule_id")) == target), None)
    created = same is None
    if created:
        proposals["issued"] += 1
        same = {"id": f"P-{proposals['issued']:03d}", **proposal}
        proposals["proposals"].append(same)
        proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
    print(json.dumps({"id": same["id"], "created": created}))
elif command == "proposals":
    print(json.dumps({"proposals": proposals["proposals"]}))
elif command == "accept":
    proposal = pending(args[0])
    if proposal["kind"] == "retire":
        rule_id = proposal["rule_id"]
        rules_file.write_text(json.dumps([r for r in rules if r["id"] != rule_id]), encoding="utf-8")
    else:
        rule_id = add_rule(proposal["rule"])
    proposals["proposals"].remove(proposal)
    proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
    print(json.dumps({"accepted": [{"proposal": proposal["id"], "kind": proposal["kind"], "rule_id": rule_id}]}))
elif command == "decline":
    proposals["proposals"].remove(pending(args[0]))
    proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
    print(f"declined {args[0]}")
elif command == "report":
    rows = [{"id": r["id"], "rule": r["rule"], "applied": 0, "hit": 0, "miss": 0, "rejected": 0, "decisions": 12} for r in rules]
    print(json.dumps({"rules": rows, "drop_candidates": [r["id"] for r in rows], "rewrite_candidates": [], "conflict_candidates": [],
                      "declined_proposals": [], "daily": [{"day": "2026-10-26"}], "new_rule_candidates": []}))
