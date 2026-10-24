"""現状を見る GUI（algr ui）。標準ライブラリの http.server で、HTML 1 枚と JSON を返す。

GUI は判断も状態も持たない。読むのはアダプタの IF とエンジンのイベントログだけで、
人の操作は CLI と同じ Engine のメソッドを通す。いまは操作を有効にしておらず、画面には同じことをする CLI を出す。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from algr import flow, stats
from algr.engine import Engine
from algr.model import State, Task

PAGE = Path(__file__).with_name("ui.html")

# 主経路。needs-decision と failed は、主経路のどこで止まっているかで見せる
STAGES = (State.INTAKE, State.READY, State.IN_PROGRESS, State.REVIEW, State.DONE)
# 寄り道の状態を、主経路のどの段に置くか
DETOUR = {State.NEEDS_DECISION: State.INTAKE}


def _progress(task: Task, events: list[dict[str, Any]], states: dict[str, State]) -> dict[str, Any]:
    """タスクがいまどこにいて、誰の手にあるか。"""
    names = [e["name"] for e in events]
    stage = DETOUR.get(task.state, task.state)
    failure = None
    if task.state == State.FAILED:
        last = next((e for e in reversed(events) if e["name"] == "task.failed"), None)
        stage = State(last["data"]["from"]) if last else State.INTAKE
        failure = {"role": last["data"]["role"], "message": last["data"]["message"]} if last else None
    blocked_by = [d for d in task.depends if states.get(d) != State.DONE] if task.state == State.READY else []
    if task.state in (State.NEEDS_DECISION, State.FAILED) or (task.state == State.REVIEW and task.approved):
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
    if task.state == State.REVIEW and task.approved:
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
        elif self.path == "/api/snapshot":
            body = json.dumps(snapshot(self.engine), ensure_ascii=False).encode("utf-8")
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
