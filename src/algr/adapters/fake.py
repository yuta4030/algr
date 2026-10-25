"""フェイクアダプタ。ファイルのタスクとローカルプロセスの実行で、algr 本体だけでループを一周させる。

外部サービスには一切つながない。実運用のアダプタを書くときの見本も兼ねる。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from algr.adapters import Conflict, Context, Rule, RunState, RunStatus
from algr.model import Event, Job, State, Task


def _write_json(path: Path, data: Any) -> None:
    """一時ファイルに書いてから置き換える。途中で落ちても壊れたファイルを残さない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _pid_alive(pid: int) -> bool:
    """pid のプロセスが生きているか。Windows の os.kill(pid, 0) は生死を見ずにプロセスを終わらせるので使わない。"""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED なら、ほかの利用者のプロセスとして生きている
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        # 自分が起動したプロセスなら刈り取る。刈り取らないとゾンビとして生きて見え続ける
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # 別の algr の起動で始めたプロセス
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
        path = self._path(task.id)
        stored = json.loads(path.read_text(encoding="utf-8")).get("version", 0) if path.exists() else 0
        if stored != task.version:
            raise Conflict(f"{task.id} was changed by someone else (version {task.version}, stored {stored})")
        task.version += 1
        _write_json(path, task.to_dict())

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
    """エージェントの代わりにローカルのコマンドを走らせる。

    コマンドには環境変数 ALGR_JOB_ID / ALGR_ROLE / ALGR_JOB_FILE / ALGR_RESULT_FILE が渡る。
    ALGR_JOB_FILE には {"id", "role", "task", "input"} が入っている。
    コマンドは ALGR_RESULT_FILE に役ごとの結果（algr.agents を参照）を JSON で書いて終了する。
    結果に "usage"（使ったトークン数）を添えると、結果から外して RunStatus.usage で返す。
    algr の起動をまたいでも状態を追えるよう、pid と終了コードはファイルに残す。
    """

    def __init__(
        self, ctx: Context, command: list[str] | None = None, max_parallel: int = 1, dir: str = ".algr/state/runs"
    ) -> None:
        self.ctx = ctx
        # 既定はフェイクのエージェント。AI を使わず決まりで判断する
        self.command = list(command) if command else [sys.executable, "-m", "algr.fake_agent"]
        self.max_parallel = max_parallel
        self.dir = ctx.path(dir)

    def _run_dir(self, job_id: str) -> Path:
        return self.dir / job_id

    def start(self, job: Job) -> None:
        run = self._run_dir(job.id)
        if (run / "pid").exists():
            return  # 冪等：一度起動したジョブは起動し直さない
        run.mkdir(parents=True, exist_ok=True)
        _write_json(run / "job.json", job.to_dict())
        env = {
            **os.environ,
            "ALGR_JOB_ID": job.id,
            "ALGR_ROLE": str(job.role),
            "ALGR_JOB_FILE": str(run / "job.json"),
            "ALGR_RESULT_FILE": str(run / "result.json"),
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "algr.adapters._runner", str(run), "--", *self.command],
            cwd=self.ctx.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # 端末の Ctrl-C がエージェントに届かないよう、包みを別のセッション（Windows はプロセスグループ）で起動する
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}),
        )
        (run / "pid").write_text(str(proc.pid), encoding="utf-8")

    def _alive(self, run: Path) -> bool:
        return _pid_alive(int((run / "pid").read_text(encoding="utf-8")))

    def status(self, job_id: str) -> RunStatus:
        run = self._run_dir(job_id)
        if not (run / "pid").exists():
            return RunStatus(RunState.FAILED, message="not started")
        if not (run / "exit").exists():
            if self._alive(run):
                return RunStatus(RunState.RUNNING)
            if not (run / "exit").exists():  # 生死を見るあいだに終わった場合を除く
                return RunStatus(RunState.FAILED, message="lost")  # 再起動などで、終了コードを残さずに消えた
        self._alive(run)  # 終わった包みを刈り取っておく
        code = int((run / "exit").read_text(encoding="utf-8").strip())
        result_path = run / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        usage = result.pop("usage", {}) if isinstance(result, dict) else {}
        if code != 0 or not result:
            # エージェントが stderr に残した原因を添える。log はジョブの出力をすべて持つので、最後の方だけ
            log = run / "log"
            tail = log.read_text(encoding="utf-8", errors="replace").strip()[-500:] if log.exists() else ""
            message = f"exit {code}: {tail}" if tail else f"exit {code}"
            return RunStatus(RunState.FAILED, result, message=message, usage=usage)
        return RunStatus(RunState.COMPLETED, result, usage=usage)

    def running(self) -> int:
        if not self.dir.exists():
            return 0
        return sum(1 for run in self.dir.iterdir() if (run / "pid").exists() and not (run / "exit").exists() and self._alive(run))

    def capacity(self) -> int:
        return max(0, self.max_parallel - self.running())

    def stop(self, job_id: str) -> None:
        run = self._run_dir(job_id)
        if (run / "pid").exists() and not (run / "exit").exists():
            pid = int((run / "pid").read_text(encoding="utf-8"))
            if os.name == "nt":
                # Windows にはセッションが無いので、プロセスの木ごと止める
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            else:
                try:
                    # 包みはセッションの頭なので、その下のエージェントごと止める
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            (run / "exit").write_text("143", encoding="utf-8")


class RuleFile:
    """1 行ルールと判断の記録をファイルで持つ。k2down の代わりに使うフェイクの判断ストア。

    rules.json に {"rules": [{"id", "text"}], "proposals": [{"id", "rule", "source"}]}、
    usage.jsonl に apply / reject / escalate / decide / propose の記録を追記する。
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

    def reject(self, decision: str, problem: str, rule_ids: list[str]) -> None:
        self._log("rejected", decision=decision, problem=problem, rule_ids=rule_ids)

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

    def reject_proposal(self, proposal_id: str) -> None:
        data = self._load()
        if not any(p["id"] == proposal_id for p in data["proposals"]):
            raise KeyError(f"no such proposal: {proposal_id}")
        data["proposals"] = [p for p in data["proposals"] if p["id"] != proposal_id]
        _write_json(self.dir / "rules.json", data)


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
