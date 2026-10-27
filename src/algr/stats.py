"""指標の集計。.algr/state/events.jsonl だけから数え、外には何も聞かない。

差し戻し率と STP 率はどちらも常に出す。差し戻し率は、根拠の薄い自己判断でも、何でもクライアントに聞くようにしても下がるので、
立場の介入回数と必ず並べて見る。実装中とレビューで聞いた回数は原因が違うので分けて数える。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

# 立場が手を入れたことを表すイベント
HUMAN_EVENTS = ("intake.answered", "implement.answered", "review.answered", "task.reopened")
# 判断待ち（waiting-*）に入るイベント
ASKED_EVENTS = ("intake.asked", "implement.asked", "review.asked")
ANSWERED_EVENTS = HUMAN_EVENTS[:3]


def load(path: Path) -> list[dict[str, Any]]:
    """イベントのほかに、ジョブの起動（"job_started"）と終了（"job"）、hook の失敗（"hook_error"）も含めて返す。"""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _is_human(event: dict[str, Any]) -> bool:
    return event["name"] in HUMAN_EVENTS


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    events = [r for r in records if "name" in r]
    jobs = [r for r in records if "job" in r]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_task.setdefault(e["task_id"], []).append(e)

    def tasks_with(name: str) -> int:
        return sum(1 for evs in by_task.values() if any(e["name"] == name for e in evs))

    done = [evs for evs in by_task.values() if any(e["name"] == "task.done" for e in evs)]
    touchless = [evs for evs in done if not any(_is_human(e) for e in evs)]
    created = tasks_with("task.created")
    interventions = sum(1 for e in events if _is_human(e))

    waits = []
    for evs in by_task.values():
        asked_at = None
        for e in evs:
            if e["name"] in ASKED_EVENTS and e["data"].get("questions"):
                asked_at = asked_at or datetime.fromisoformat(e["ts"])
            elif e["name"] in ANSWERED_EVENTS and asked_at is not None:
                waits.append((datetime.fromisoformat(e["ts"]) - asked_at).total_seconds())
                asked_at = None

    by_kind: dict[str, int] = {}
    for j in jobs:
        for kind, n in j.get("usage", {}).items():
            by_kind[kind] = by_kind.get(kind, 0) + n

    return {
        "tasks": created,
        "done": len(done),
        "failed": tasks_with("task.failed"),
        "return_rate_implement": _rate(tasks_with("implement.asked"), tasks_with("task.started")),
        "return_rate_review": _rate(tasks_with("review.asked"), tasks_with("task.submitted")),
        "rework_rate": _rate(tasks_with("review.changes_requested"), tasks_with("task.submitted")),
        "stp_rate": _rate(len(touchless), len(done)),
        "interventions_per_task": _rate(interventions, created),
        "decision_wait_median_s": median(waits) if waits else None,
        "tokens_per_task": _rate(sum(by_kind.values()), created),
        "tokens_per_task_by_kind": {kind: _rate(n, created) for kind, n in by_kind.items()},
    }
