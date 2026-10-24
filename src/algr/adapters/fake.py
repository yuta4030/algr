"""フェイクアダプタ。ファイルのタスクとローカルプロセスの実行で、algr 本体だけでループを一周させる。

外部サービスには一切つながない。実運用のアダプタを書くときの見本も兼ねる。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from algr.adapters import Context, Rule, RunState, RunStatus
from algr.model import Event, State, Task


def _write_json(path: Path, data: Any) -> None:
    """一時ファイルに書いてから置き換える。途中で落ちても壊れたファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


class FileTasks:
    """タスクを 1 件 1 ファイルの JSON で持つ。ID は I1, I2, ... と振る。"""

    def __init__(self, ctx: Context, dir: str = ".algr/tasks") -> None:
        self.dir = ctx.path(dir)

    def _path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.json"

    def create(self, task: Task) -> Task:
        numbers = [int(m.group(1)) for p in self.dir.glob("I*.json") if (m := re.fullmatch(r"I(\d+)\.json", p.name))]
        task.id = f"I{max(numbers, default=0) + 1}"
        self.save(task)
        return task

    def get(self, task_id: str) -> Task:
        path = self._path(task_id)
        if not path.exists():
            raise KeyError(f"no such task: {task_id}")
        return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self, state: State | None = None) -> list[Task]:
        tasks = [Task.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in self.dir.glob("I*.json")]
        tasks.sort(key=lambda t: int(t.id[1:]))
        return [t for t in tasks if state is None or t.state == state]

    def save(self, task: Task) -> None:
        _write_json(self._path(task.id), task.to_dict())

    def comment(self, task_id: str, text: str) -> None:
        _append_jsonl(self.dir / f"{task_id}.comments.jsonl", {"text": text})

    def comments(self, task_id: str) -> list[str]:
        path = self.dir / f"{task_id}.comments.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines()]


class InboxTrigger:
    """inbox ディレクトリに置かれた JSON（{"name", "task_id", "data"?}）をイベントにする。読んだら消す。"""

    def __init__(self, ctx: Context, dir: str = ".algr/state/inbox") -> None:
        self.dir = ctx.path(dir)

    def push(self, event: Event) -> None:
        _write_json(self.dir / f"{time.time_ns()}-{event.name}.json", event.to_dict())

    def poll(self) -> list[Event]:
        if not self.dir.exists():
            return []
        events = []
        for path in sorted(self.dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            events.append(Event(name=data["name"], task_id=data["task_id"], data=data.get("data", {})))
            path.unlink()
        return events


class LocalProcess:
    """実装エージェントの代わりにローカルのコマンドを走らせる。

    コマンドには環境変数 ALGR_TASK_ID / ALGR_TASK_FILE / ALGR_RESULT_FILE が渡る。
    コマンドは ALGR_RESULT_FILE に {"pr": "...", "decisions": [...]} か
    {"returned": {"text": "...", "topic": "..."}} を書いて終了する。
    algr の起動をまたいでも状態を追えるよう、終了コードはファイルに残す。
    """

    def __init__(self, ctx: Context, command: list[str], max_parallel: int = 1, dir: str = ".algr/state/runs") -> None:
        self.ctx = ctx
        self.command = list(command)
        self.max_parallel = max_parallel
        self.dir = ctx.path(dir)

    def _run_dir(self, task: Task) -> Path:
        return self.dir / task.id

    def start(self, task: Task) -> None:
        run = self._run_dir(task)
        if (run / "pid").exists() and not (run / "exit").exists():
            return  # 冪等：走っているものは起動し直さない
        run.mkdir(parents=True, exist_ok=True)
        for name in ("exit", "result.json"):
            (run / name).unlink(missing_ok=True)
        _write_json(run / "task.json", task.to_dict())
        env = {
            **os.environ,
            "ALGR_TASK_ID": task.id,
            "ALGR_TASK_FILE": str(run / "task.json"),
            "ALGR_RESULT_FILE": str(run / "result.json"),
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "algr.adapters._runner", str(run), "--", *self.command],
            cwd=self.ctx.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        (run / "pid").write_text(str(proc.pid), encoding="utf-8")

    def status(self, task: Task) -> RunStatus:
        run = self._run_dir(task)
        if not (run / "pid").exists():
            return RunStatus(RunState.FAILED, message="not started")
        if not (run / "exit").exists():
            return RunStatus(RunState.RUNNING)
        code = int((run / "exit").read_text(encoding="utf-8").strip())
        result_path = run / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        if code != 0 or not result:
            return RunStatus(RunState.FAILED, result, message=f"exit {code}")
        if "returned" in result:
            return RunStatus(RunState.BLOCKED, result)
        return RunStatus(RunState.COMPLETED, result)

    def running(self) -> int:
        if not self.dir.exists():
            return 0
        return sum(1 for run in self.dir.iterdir() if (run / "pid").exists() and not (run / "exit").exists())

    def capacity(self) -> int:
        return max(0, self.max_parallel - self.running())

    def stop(self, task: Task) -> None:
        run = self._run_dir(task)
        if (run / "pid").exists() and not (run / "exit").exists():
            try:
                os.kill(int((run / "pid").read_text(encoding="utf-8")), 15)
            except ProcessLookupError:
                pass
            (run / "exit").write_text("143", encoding="utf-8")


class RuleFile:
    """1 行ルールと判断の記録をファイルで持つ。k2down の代わりに使うフェイクの判断ストア。

    rules.json に {"rules": [{"id", "text"}], "proposals": [{"id", "rule", "source"}]}、
    usage.jsonl に apply / escalate / decide / propose の記録を追記する。
    """

    def __init__(self, ctx: Context, dir: str = ".algr/decisions") -> None:
        self.dir = ctx.path(dir)

    def _load(self) -> dict[str, Any]:
        path = self.dir / "rules.json"
        if not path.exists():
            return {"rules": [], "proposals": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _log(self, event: str, **fields: Any) -> None:
        _append_jsonl(self.dir / "usage.jsonl", {"event": event, **fields})

    def usage(self) -> list[dict[str, Any]]:
        path = self.dir / "usage.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def rules(self) -> list[Rule]:
        return [Rule(r["id"], r["text"]) for r in self._load()["rules"]]

    def add(self, text: str) -> str:
        data = self._load()
        rule_id = f"R{len(data['rules']) + 1:03d}"
        data["rules"].append({"id": rule_id, "text": text})
        _write_json(self.dir / "rules.json", data)
        return rule_id

    def apply(self, decision: str, problem: str, rule_ids: list[str]) -> None:
        self._log("applied", decision=decision, problem=problem, rule_ids=rule_ids)

    def escalate(self, decision: str, problem: str, reason: str, rule_ids: list[str] | None = None) -> None:
        self._log("escalated", decision=decision, problem=problem, reason=reason, rule_ids=rule_ids or [])

    def decide(self, decision: str, outcome: str, actual: str, predicted: str | None = None) -> None:
        self._log("decided", decision=decision, outcome=outcome, actual=actual, predicted=predicted)

    def propose(self, rule: str, source: str) -> str:
        data = self._load()
        for p in data["proposals"]:
            if p["rule"] == rule:
                return p["id"]  # 冪等：同じ提案は二重に出さない
        proposal_id = f"P{len(data['proposals']) + 1:03d}"
        data["proposals"].append({"id": proposal_id, "rule": rule, "source": source})
        _write_json(self.dir / "rules.json", data)
        self._log("proposed", proposal=proposal_id, rule=rule, source=source)
        return proposal_id

    def proposals(self) -> list[dict[str, str]]:
        return self._load()["proposals"]

    def accept(self, proposal_id: str) -> str:
        """提案を受け入れてルールにする。実運用ではルールの PR のマージにあたる。"""
        data = self._load()
        proposal = next((p for p in data["proposals"] if p["id"] == proposal_id), None)
        if proposal is None:
            raise KeyError(f"no such proposal: {proposal_id}")
        data["proposals"].remove(proposal)
        _write_json(self.dir / "rules.json", data)
        return self.add(proposal["rule"])


class StubNotifier:
    """通知のスタブ。送る代わりにファイルへ追記する。"""

    def __init__(self, ctx: Context, file: str = ".algr/state/notifications.jsonl") -> None:
        self.file = ctx.path(file)

    def notify(self, task: Task, message: str) -> None:
        _append_jsonl(self.file, {"task_id": task.id, "message": message})

    def sent(self) -> list[dict[str, str]]:
        if not self.file.exists():
            return []
        return [json.loads(line) for line in self.file.read_text(encoding="utf-8").splitlines()]
