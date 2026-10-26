"""現状を見る GUI（algr ui）。標準ライブラリの http.server で、HTML 1 枚と JSON を返す。

GUI は判断も状態も持たない。読むのはアダプタの IF とエンジンのイベントログだけで、
人の操作は CLI と同じ Engine のメソッドを通す。いまは操作を有効にしておらず、画面には同じことをする CLI を出す。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from algr import flow, stats
from algr.agents import ROLE_HELP
from algr.engine import Engine
from algr.model import Role, State, Task

PAGE = Path(__file__).with_name("ui.html")
PROMPTS = Path(__file__).with_name("prompts")
# コンセプト・戦略・戦術の企画書。ヘルプはなぜこうしているかを書き写さず、ここへ案内する
CONCEPT_URL = "https://claude.ai/artifact/JpCoJPm1kdmhNiFFtZShYG"

# 主経路。needs-decision と failed は、主経路のどこで止まっているかで見せる
STAGES = (State.INTAKE, State.READY, State.IN_PROGRESS, State.REVIEW, State.DONE)
# 寄り道の状態を、主経路のどの段に置くか
DETOUR = {State.NEEDS_DECISION: State.INTAKE, State.APPROVED: State.REVIEW}


def _progress(task: Task, events: list[dict[str, Any]], states: dict[str, State]) -> dict[str, Any]:
    """タスクがいまどこにいて、誰の手にあるか。"""
    names = [e["name"] for e in events]
    stage = DETOUR.get(task.state, task.state)
    failure = task.failure if task.state == State.FAILED else None
    if task.state == State.FAILED:
        last = next((e for e in reversed(events) if e["name"] == "task.failed"), None)
        stage = State(last["data"]["from"]) if last else State.INTAKE
    blocked_by = [d for d in task.depends if states.get(d) != State.DONE] if task.state == State.READY else []
    if task.state in (State.NEEDS_DECISION, State.FAILED) or task.state == State.APPROVED:
        waiting = "human"
    elif task.state == State.DONE:
        waiting = "none"
    elif blocked_by:
        waiting = "deps"
    elif task.jobs and set(task.jobs) != {"distill"}:
        waiting = "agent"
    else:
        waiting = "queue"
    return {
        "stage": str(stage),
        "waiting": waiting,
        "roles": sorted(task.jobs),
        "blocked_by": blocked_by,
        "failure": failure,
        "created": events[0]["ts"] if events else None,
        "since": events[-1]["ts"] if events else None,
        "returned": names.count("task.returned"),
        "changes": names.count("review.changes_requested"),
        "asked": names.count("decision.requested") + names.count("review.escalated"),
    }


def _actions(task: Task) -> list[dict[str, str]]:
    """人ができる操作。GUI ではまだ押せないので、同じことをする CLI を添える。"""
    if task.state == State.NEEDS_DECISION:
        answers = " ".join(f'{q.id}="{q.recommended or "..."}"' for q in task.open_questions())
        return [{"name": "answer", "cli": f"algr answer {task.id} {answers}"}]
    if task.state == State.FAILED:
        return [{"name": "reopen", "cli": f'algr reopen {task.id} --note "..."'}]
    if task.state == State.APPROVED:
        return [{"name": "merge", "cli": f"algr merge {task.id}"}]
    return []


def snapshot(engine: Engine) -> dict[str, Any]:
    """画面に要るものを 1 つの JSON にまとめる。"""
    records = stats.load(engine.event_log) if engine.event_log else []
    events = [r for r in records if "name" in r]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_task.setdefault(e["task_id"], []).append(e)
    tasks = engine.tasks.list()
    states = {t.id: t.state for t in tasks}
    return {
        "now": datetime.now(UTC).isoformat(timespec="seconds"),
        "flow": {
            "stages": [str(s) for s in STAGES],
            "states": [str(s) for s in State],
            "events": list(flow.EVENTS),
            "merge_gate": engine.merge_gate,
        },
        "tasks": [
            {
                **t.to_dict(),
                "decisions": [{**asdict(d), "by": t.decided_by(d)} for d in t.decisions],
                "progress": _progress(t, by_task.get(t.id, []), states),
                "actions": _actions(t),
                "events": by_task.get(t.id, []),
            }
            for t in tasks
        ],
        "events": events[-50:],
        "stats": stats.summarize(records),
        "rules": [{"id": r.id, "text": r.text} for r in engine.decisions.rules()],
        "proposals": engine.decisions.proposals(),
    }


# --- サブページ ---------------------------------------------------------


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _by_task(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if "task_id" in r:
            out.setdefault(r["task_id"], []).append(r)
    return out


def trend(records: list[dict[str, Any]]) -> dict[str, Any]:
    """起票した日でタスクを束ね、束ごとに指標を出す。3 週間を超えたら週で、それまでは日で束ねる。"""
    groups = _by_task(records)
    created = {
        task_id: _ts(r["ts"]).date()
        for task_id, rs in groups.items()
        for r in rs
        if r.get("name") == "task.created"
    }
    if not created:
        return {"unit": "day", "buckets": []}
    weekly = (max(created.values()) - min(created.values())).days > 21

    def bucket(d: date) -> date:
        return d - timedelta(days=d.weekday()) if weekly else d

    step = timedelta(days=7 if weekly else 1)
    first, last = bucket(min(created.values())), bucket(max(created.values()))
    buckets = []
    day = first
    while day <= last:
        ids = [t for t, d in created.items() if bucket(d) == day]
        buckets.append({"start": day.isoformat(), **stats.summarize([r for t in ids for r in groups[t]])})
        day += step
    return {"unit": "week" if weekly else "day", "buckets": buckets}


def flow_counts(records: list[dict[str, Any]], tasks: list[Task]) -> dict[str, Any]:
    """遷移ごとの通過回数と、状態ごとの滞在時間の中央値。遷移の定義は flow.TRANSITIONS から取る。"""
    events = [r for r in records if "name" in r]
    counts: dict[tuple[str, str], int] = {}
    for e in events:
        if e["name"] != "task.created":
            key = (e["name"], e["data"]["from"])
            counts[key] = counts.get(key, 0) + 1
    edges = [
        {"event": name, "from": str(src), "to": str(target), "count": counts.get((name, str(src)), 0)}
        for name, (sources, target) in flow.TRANSITIONS.items()
        for src in sorted(sources)
    ]
    stays: dict[str, list[float]] = {}
    for rs in _by_task(events).values():
        for e, nxt in pairwise(rs):
            state = str(flow.TRANSITIONS[e["name"]][1])
            stays.setdefault(state, []).append((_ts(nxt["ts"]) - _ts(e["ts"])).total_seconds())
    states = [
        {
            "state": str(s),
            "now": sum(1 for t in tasks if t.state == s),
            "stay_median_s": median(stays[str(s)]) if stays.get(str(s)) else None,
        }
        for s in State
    ]
    return {"edges": edges, "states": states}


def rule_usage(tasks: list[Task], rules: list[dict[str, str]], proposals: list[dict[str, str]]) -> dict[str, Any]:
    """ルールがどこで使われたか、まだルールになっていない判断はどこにあるか。"""
    used: dict[str, list[dict[str, str]]] = {r["id"]: [] for r in rules}
    topics: dict[str, dict[str, Any]] = {}
    questions = {}
    for t in tasks:
        for q in t.questions:
            questions[q.id] = {"text": q.text, "answer": q.answer}
            for rid in q.rule_ids:
                used.setdefault(rid, []).append({"task_id": t.id, "id": q.id})
            if q.by == "human":
                topic = topics.setdefault(q.topic, {"topic": q.topic, "human": 0, "agent": 0, "examples": []})
                topic["human"] += 1
                topic["examples"].append({"id": q.id, "text": q.text, "answer": q.answer, "by": "human"})
        for d in t.decisions:
            for rid in d.rule_ids:
                used.setdefault(rid, []).append({"task_id": t.id, "id": d.id})
            if t.decided_by(d) == "agent":
                topic = topics.setdefault(d.topic, {"topic": d.topic, "human": 0, "agent": 0, "examples": []})
                topic["agent"] += 1
                topic["examples"].append({"id": d.id, "text": d.text, "answer": d.answer, "by": "agent"})
    return {
        "rules": [{**r, "uses": used.get(r["id"], [])} for r in rules],
        # 人が何度も答えている、またはエージェントが根拠なしで決めている topic は、ルールにする候補
        "candidates": sorted(topics.values(), key=lambda c: -(c["human"] + c["agent"])),
        "proposals": [{**p, "question": questions.get(p.get("decision", ""))} for p in proposals],
    }


def jobs(records: list[dict[str, Any]], tasks: list[Task]) -> list[dict[str, Any]]:
    """ジョブごとの開始・終了とトークン数。終わっていないジョブは end が None。"""
    titles = {t.id: t.title for t in tasks}
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        if "job_started" in r:
            out[r["job_started"]] = {
                "job": r["job_started"], "task_id": r["task_id"], "title": titles.get(r["task_id"], ""),
                "role": r["role"], "start": r["ts"], "end": None, "state": "running", "tokens": 0,
            }  # fmt: skip
        elif "job" in r and r["job"] in out:
            out[r["job"]].update(end=r.get("ts"), state=r["state"], tokens=sum(r.get("usage", {}).values()))
    return list(out.values())


def history(engine: Engine) -> dict[str, Any]:
    records = stats.load(engine.event_log) if engine.event_log else []
    tasks = engine.tasks.list()
    rules = [{"id": r.id, "text": r.text} for r in engine.decisions.rules()]
    return {
        "now": datetime.now(UTC).isoformat(timespec="seconds"),
        "trend": trend(records),
        "flow": flow_counts(records, tasks),
        "rules": rule_usage(tasks, rules, engine.decisions.proposals()),
        "jobs": jobs(records, tasks),
    }


def help_page() -> dict[str, Any]:
    """algr 自身の説明。文章は書き起こさず、状態・遷移・不変条件・役・プロンプト・コマンドをコードから組み立てる。"""
    from algr.cli import STANCES, commands  # cli が ui を読むので、ここで読む

    return {
        "concept": CONCEPT_URL,
        "stances": [{"name": name, "commands": list(names)} for name, names in STANCES.items()],
        "states": [{"state": str(s), "help": flow.STATE_HELP[s]} for s in State],
        "events": [
            {
                "name": name,
                "from": sorted(str(s) for s in sources),
                "to": str(target),
                "data": list(flow.EVENT_DATA[name]),
                "help": flow.EVENT_HELP[name],
                "invariants": [inspect.getdoc(f) for f in flow.INVARIANTS.get(name, ())],
            }
            for name, (sources, target) in flow.TRANSITIONS.items()
        ],
        "roles": [{"role": str(r), **ROLE_HELP[r], "prompt": (PROMPTS / f"{r}.md").read_text(encoding="utf-8")} for r in Role],
        "common_prompt": (PROMPTS / "common.md").read_text(encoding="utf-8"),
        "commands": commands(),
    }


class _Handler(BaseHTTPRequestHandler):
    engine: Engine
    hosts: set[str]

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        # ローカル専用。ほかの名前で来たリクエスト（DNS rebinding）は受けない
        if self.headers.get("Host") not in self.hosts:
            self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        elif self.path == "/":
            self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path in ("/api/snapshot", "/api/history", "/api/help"):
            build = {"/api/snapshot": snapshot, "/api/history": history}.get(self.path)
            body = json.dumps(build(self.engine) if build else help_page(), ensure_ascii=False).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")


def server(engine: Engine, port: int = 8765) -> ThreadingHTTPServer:
    """127.0.0.1 にだけ bind する。port に 0 を渡すと空いている番号を使う。"""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    bound = httpd.server_address[1]
    attrs = {"engine": engine, "hosts": {f"127.0.0.1:{bound}", f"localhost:{bound}"}}
    httpd.RequestHandlerClass = type("Handler", (_Handler,), attrs)
    return httpd
