"""フェイクのエージェント。fake.process の既定のコマンドで、algr 本体だけでループを一周させる。

python -m algr.fake_agent で起動され、ALGR_ROLE の役を演じる。AI は使わず、次の決まりで判断する。
- 起票：「<topic>: <answer>」の形のルールを、同じ topic の未決事項に当てる
- 実装：判断なしで PR を出す
- レビュー：PR があり、判断がすべて実在するルールか人の答えに基づいていれば通す
- 蒸留：topic のある人の答えを「<topic>: <answer>」のルールにする
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

RULE_SEPARATOR = ": "


def intake(task: dict[str, Any], rules: list[dict[str, str]]) -> dict[str, Any]:
    fills = []
    for q in task["questions"]:
        if q["answer"] is not None or not q["topic"]:
            continue
        for rule in rules:
            topic, sep, answer = rule["text"].partition(RULE_SEPARATOR)
            if sep and topic == q["topic"]:
                fills.append({"question_id": q["id"], "answer": answer, "rule_ids": [rule["id"]]})
                break
    return {"fills": fills, "questions": []}


def implement(task: dict[str, Any], rules: list[dict[str, str]]) -> dict[str, Any]:
    return {"pr": f"pr/{task['id']}", "decisions": []}


def review(task: dict[str, Any], rules: list[dict[str, str]]) -> dict[str, Any]:
    known = {r["id"] for r in rules}
    objections = {}
    for d in task["decisions"]:
        if not d["rule_ids"]:
            objections[d["id"]] = "decided without a rule"
        elif not set(d["rule_ids"]) <= known:
            objections[d["id"]] = "cites unknown rules"
    for q in task["questions"]:
        if q["by"] == "rule" and not set(q["rule_ids"]) <= known:
            objections[q["id"]] = "cites unknown rules"
    return {"approved": bool(task["pr"]) and not objections, "objections": objections}


def distill(questions: list[dict[str, Any]]) -> dict[str, Any]:
    rules = [
        {"rule": f"{q['topic']}{RULE_SEPARATOR}{q['answer']}", "source": q["id"]}
        for q in questions
        if q["topic"] and q["answer"] is not None
    ]
    return {"rules": rules}


def run(job: dict[str, Any]) -> dict[str, Any]:
    task, rules = job["task"], job["input"].get("rules", [])
    role = job["role"]
    if role == "intake":
        return intake(task, rules)
    if role == "implement":
        return implement(task, rules)
    if role == "review":
        return review(task, rules)
    if role == "distill":
        return distill(job["input"].get("questions", []))
    raise SystemExit(f"unknown role: {role}")


def main() -> int:
    with open(os.environ["ALGR_JOB_FILE"], encoding="utf-8") as f:
        job = json.load(f)
    result = run(job)
    with open(os.environ["ALGR_RESULT_FILE"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
