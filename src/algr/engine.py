"""フローのコア。イベントを起こして状態を動かし、イベントごとの既定の処理と hook を呼ぶ。

外部には必ずアダプタ越しに触る。エージェントはすべて実行アダプタがジョブとして起動し、
コアはタスクの状態から「いま要るジョブ」を出して起動し、結果を受け取って状態を動かすだけ。
"""

from __future__ import annotations

import json
import signal
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from algr import flow
from algr.adapters import (
    AdapterError,
    Conflict,
    DecisionStore,
    ExecutorAdapter,
    MergeAdapter,
    MergeState,
    Notifier,
    Rule,
    RunState,
    RunStatus,
    TaskAdapter,
    TriggerAdapter,
)
from algr.agents import DistillResult, ImplementResult, IntakeResult, ResultError, ReviewResult, job_input, parse
from algr.config import Config
from algr.model import Event, Job, Question, Role, State, Task

Hook = Callable[[Event, "Engine"], Any]

# トリガーが外から送れるイベント。立場の操作（起票、回答、やり直し、やめる）と同じ入口を通る。
# 回答はどの役が聞いたかを知らなくてよいよう task.answered で受け、判断待ちの状態から <役>.answered を起こす
EXTERNAL_EVENTS = ("task.created", "task.answered", "task.reopened", "task.canceled")

# エージェントが起票する別件の未決事項から受け取る項目
QUESTION_FIELDS = ("text", "topic", "options", "recommended")

# 立場の方針を蒸留に依頼するタスクで、方針（答え）を載せる未決事項の問い
POLICY_QUESTION = "人の方針（タスクの外で受けたもの）"

# 状態ごとに、その状態を先へ進めるエージェントの役
ROLE_FOR_STATE = {State.INTAKE: Role.INTAKE, State.IN_PROGRESS: Role.IMPLEMENT, State.REVIEW: Role.REVIEW}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def _retry_next_tick() -> Iterator[None]:
    """保存がぶつかったら、そのタスクはこの tick では諦める。保存されていないので、次の tick で読み直してやり直せる。"""
    try:
        yield
    except Conflict:
        pass


class Engine:
    def __init__(
        self,
        *,
        tasks: TaskAdapter,
        trigger: TriggerAdapter,
        executor: ExecutorAdapter,
        decisions: DecisionStore,
        merger: MergeAdapter,
        notifier: Notifier,
        hooks: dict[str, list[Hook]] | None = None,
        max_parallel: int = 1,
        max_rework: int = 2,
        agents: dict[str, dict[str, Any]] | None = None,
        event_log: Path | None = None,
    ) -> None:
        self.tasks = tasks
        self.trigger = trigger
        self.executor = executor
        self.decisions = decisions
        self.merger = merger
        self.notifier = notifier
        self.hooks = hooks or {}
        self.max_parallel = max_parallel
        self.max_rework = max_rework
        self.agents = agents or {}
        self.event_log = event_log
        self.events: list[Event] = []
        self._pending: deque[Event] = deque()
        self._draining = False
        for name in self.hooks:
            if name not in flow.HOOK_POINTS:
                raise flow.TransitionError(f"hook for unknown event: {name}")

    @classmethod
    def from_config(cls, config: Config) -> Engine:
        return cls(
            tasks=config.adapter("task"),
            trigger=config.adapter("trigger"),
            executor=config.adapter("executor"),
            decisions=config.adapter("decisions"),
            merger=config.adapter("merge"),
            notifier=config.adapter("notify"),
            hooks=config.hooks(),
            max_parallel=config.max_parallel,
            max_rework=config.max_rework,
            agents=config.agents(),
            event_log=config.ctx.path(".algr/state/events.jsonl"),
        )

    # --- イベント ---------------------------------------------------------

    def emit(self, name: str, task: Task, **data: Any) -> Event:
        """遷移を確かめて状態を動かし、保存してからイベントを積む。処理は積んだ順に 1 つずつ行う。"""
        target = flow.check(name, task)
        data = {"from": "" if name == "task.created" else str(task.state), **data}
        flow.check_data(name, data)
        task.state = target
        self.tasks.save(task)
        event = Event(name, task.id, data)
        self.events.append(event)
        self._log(event.to_dict())
        self._pending.append(event)
        self._drain()
        return event

    def _drain(self) -> None:
        # ハンドラの中で起きたイベントは、今のイベントの hook が済んでから処理する
        if self._draining:
            return
        self._draining = True
        try:
            while self._pending:
                event = self._pending.popleft()
                handler = getattr(self, "_on_" + event.name.replace(".", "_"))
                with _retry_next_tick():  # ハンドラが起動しそこねたジョブは、次の tick の _start_jobs が起動する
                    handler(self.tasks.get(event.task_id), event)
                for hook in [*self.hooks.get(event.name, []), *self.hooks.get("*", [])]:
                    self._call_hook(hook, event)
        finally:
            self._draining = False

    def _call_hook(self, hook: Hook, event: Event) -> None:
        """hook は既定の処理のあとに呼ぶ。状態はもう動いているので、hook が落ちてもフローは止めずに知らせる。"""
        try:
            hook(event, self)
        except Exception as e:  # noqa: BLE001 — 利用側のコードの失敗でループを止めない
            name = getattr(hook, "__qualname__", repr(hook))
            message = f"hook {name} failed on {event.name}: {type(e).__name__}: {e}"
            self._log({"hook_error": message, "event": event.to_dict()})
            self.notifier.notify(self.tasks.get(event.task_id), f"{event.task_id} {message}")

    def _log(self, record: dict[str, Any]) -> None:
        if self.event_log:
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- 立場やトリガーからの入口 -----------------------------------------

    def create(
        self,
        title: str,
        *,
        purpose: str = "",
        questions: list[dict[str, Any]] | None = None,
        source: str = "human",
        priority: int = 0,
        depends: list[str] | None = None,
    ) -> Task:
        """意図を受け取り、タスクを起票に流す。questions はクライアントが最初から分かっている未決事項。"""
        for dep in depends or []:
            self.tasks.get(dep)  # 無いタスクには依存できない
        task = Task(id="", title=title, purpose=purpose, source=source, priority=priority, depends=list(depends or []))
        task = self.tasks.create(task)
        for q in questions or []:
            task.add_question(**q)
        self.emit("task.created", task, source=source)
        return self.tasks.get(task.id)

    def resolve(self, task_id: str, answers: dict[str, str]) -> Task:
        """クライアントが未決事項に答える。答えがそろうと聞いた役の状態に戻り、その役のジョブが要約から走り直す。

        答えは一部ずつでもよい。残りがあるあいだは答えだけを保存し、判断待ちのまま待つ。
        """
        task = self.tasks.get(task_id)
        if task.state not in flow.WAITING:
            raise flow.TransitionError(f"{task_id} is {task.state}, not waiting for a decision")
        for qid, answer in answers.items():
            q = task.question(qid)
            if not q.open:
                raise flow.TransitionError(f"{qid} is already decided")
            q.answer, q.by = answer, "human"
            self.decisions.decide(q.id, "none", answer)
            task.to_distill.append(q.id)
        if task.open_questions():
            self.tasks.save(task)
        else:
            self.emit(flow.WAITING[task.state][1], task, questions=list(answers))
        return self.tasks.get(task_id)

    def request_distill(self, policy: str, *, topic: str = "") -> Task:
        """メタプログラマーがタスクの外で受けた方針を、蒸留エージェントに依頼する。

        依頼はタスクに乗せる（アダプタ IF を変えずにジョブを持てる）。方針は立場が答えた未決事項として持ち、
        蒸留のジョブだけが出るよう、最初から done にしておく。起票・実装・レビューには流れず、イベントも出さない。
        """
        title = f"方針：{' '.join(policy.split())[:40]}"
        task = self.tasks.create(Task(id="", title=title, purpose=policy, source="policy", state=State.DONE))
        q = task.add_question(POLICY_QUESTION, topic, answer=policy, by="human")
        self.decisions.decide(q.id, "none", policy)
        task.to_distill.append(q.id)
        self.tasks.save(task)
        self._start_jobs(task)
        return self.tasks.get(task.id)

    def reopen(self, task_id: str, note: str = "") -> Task:
        """クライアントが failed のタスクを起票に戻す。依頼を通せなかったので、やり直すかは依頼した側が決める。修正依頼の回数は数え直す。"""
        task = self.tasks.get(task_id)
        if task.state != State.FAILED:
            raise flow.TransitionError(f"{task_id} is {task.state}, not failed")
        task.reworks, task.failure = 0, None
        self.emit("task.reopened", task, note=note)
        return self.tasks.get(task_id)

    def cancel(self, task_id: str, note: str = "") -> Task:
        """クライアントがタスクをやめる。目的を変えるときも、やめて new し直す。マージ先に入った（done）ものはやめられない。"""
        task = self.tasks.get(task_id)
        if task.state in (State.DONE, State.CANCELED):
            raise flow.TransitionError(f"{task_id} is {task.state} and cannot be canceled")
        self.emit("task.canceled", task, note=note)
        return self.tasks.get(task_id)

    def depend(self, task_id: str, *, add: list[str] | None = None, remove: list[str] | None = None) -> Task:
        """クライアントが依存先を足す・外す。依存先は実在し、依存をたどって自分に戻らないこと。配車は次の tick から新しい依存で判定する。"""
        task = self.tasks.get(task_id)
        for dep in add or []:
            self.tasks.get(dep)  # 無いタスクには依存できない
        if unknown := sorted(set(remove or []) - set(task.depends)):
            raise flow.TransitionError(f"{task_id} does not depend on {' '.join(unknown)}")
        depends = [d for d in task.depends if d not in (remove or [])]
        depends += [d for d in dict.fromkeys(add or []) if d not in depends]
        if cycle := self._cycle(task_id, depends):
            raise flow.TransitionError(f"depends would form a cycle: {' -> '.join(cycle)}")
        task.depends = depends
        self.tasks.save(task)
        return self.tasks.get(task_id)

    def _cycle(self, task_id: str, depends: list[str]) -> list[str]:
        """task_id が depends を持つとしたときに、依存をたどって task_id に戻る道。無ければ空。"""
        graph = {t.id: t.depends for t in self.tasks.list()} | {task_id: depends}
        stack, seen = [[task_id, dep] for dep in depends], set()
        while stack:
            path = stack.pop()
            if path[-1] == task_id:
                return path
            if path[-1] not in seen:
                seen.add(path[-1])
                stack += [[*path, dep] for dep in graph.get(path[-1], [])]
        return []

    def tick(self) -> list[Event]:
        """1 周分：トリガーのイベントを取り込み、ジョブの結果を受け取り、マージを見て、ready を配車し、要るジョブを起動する。"""
        start = len(self.events)
        for event in self.trigger.poll():
            self.handle_external(event)
        self.poll()
        for task in self.tasks.list(State.MERGING):
            with _retry_next_tick():
                self._merge(task)
        self.dispatch()
        for task in self.tasks.list():
            with _retry_next_tick():
                self._start_jobs(task)
        return self.events[start:]

    def run(self, interval: float = 0.5, timeout: float | None = None) -> list[Event]:
        """走っているジョブがなくなるまで tick を回す。常駐はせず、手が空いたら戻る。"""
        start, deadline = len(self.events), None if timeout is None else time.monotonic() + timeout
        while True:
            self.tick()
            if not any(t.jobs for t in self.tasks.list()):
                return self.events[start:]
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"jobs still running after {timeout}s")
            time.sleep(interval)

    def watch(
        self,
        interval: float = 5.0,
        on_events: Callable[[list[Event]], None] | None = None,
        until: Callable[[], bool] | None = None,
    ) -> None:
        """常駐して tick を回し続ける。トリガーの取り込みと ready の配車が止まらないようにする。

        until が真を返すか、SIGINT（Ctrl-C）か SIGTERM（Windows は SIGBREAK も）を受けると、いまの tick を終えてから止まる。
        保存がぶつかっても次の tick でやり直すので、algr answer などの立場の操作と同時に動かしてよい。
        """
        stopping: list[int] = []
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])):
                previous[sig] = signal.signal(sig, lambda signum, frame: stopping.append(signum))
        try:
            while not stopping and (until is None or not until()):
                events = self.tick()
                if events and on_events:
                    on_events(events)
                deadline = time.monotonic() + interval
                while not stopping and time.monotonic() < deadline:
                    time.sleep(min(0.1, interval))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def handle_external(self, event: Event) -> None:
        if event.name == "task.created":
            self.create(**event.data)
        elif event.name == "task.answered":
            self.resolve(event.task_id, event.data["answers"])
        elif event.name == "task.reopened":
            self.reopen(event.task_id, event.data.get("note", ""))
        elif event.name == "task.canceled":
            self.cancel(event.task_id, event.data.get("note", ""))
        else:
            raise flow.TransitionError(f"triggers may only send {', '.join(EXTERNAL_EVENTS)}, got {event.name}")

    def dispatch(self) -> list[Task]:
        """ready のタスクを優先度順に、同時実行数の上限まで始める。"""
        started = []
        states = {t.id: t.state for t in self.tasks.list()}
        for task in sorted(self.tasks.list(State.READY), key=lambda t: -t.priority):
            if len(self.tasks.list(State.IN_PROGRESS)) >= self.max_parallel:
                break
            if any(states.get(dep) != State.DONE for dep in task.depends):
                continue  # 依存先がすべて done になるまで配車しない
            with _retry_next_tick():
                self.emit("task.started", task)
                started.append(task)
        return started

    # --- マージ ------------------------------------------------------------

    def _merge(self, task: Task) -> None:
        """merging のタスクのマージを頼み（冪等）、マージ先に入ったかを見る。アダプタが失敗したら次の tick でやり直す。"""
        try:
            self.merger.request(task)
            status = self.merger.status(task)
        except AdapterError as e:
            self._log({"merge_error": str(e), "task_id": task.id, "ts": _now()})
            return
        if status.state == MergeState.MERGED:
            self.emit("task.done", task, pr=task.pr)
        elif status.state == MergeState.CHANGES:
            # PJ の修正依頼は、algr のレビューが見逃したものとして実装に戻す。max_rework には数えない（立場の外の手続きなので）
            task.feedback = list(status.changes)
            self.emit("merge.changes_requested", task, changes=list(status.changes))
        elif status.state == MergeState.CLOSED:
            self._fail(task, "merge", status.message or "closed without merging")

    # --- ジョブ ------------------------------------------------------------

    def _wanted(self, task: Task) -> list[Role]:
        roles = [ROLE_FOR_STATE[task.state]] if task.state in ROLE_FOR_STATE else []
        # 蒸留はタスクが done になってから 1 回にまとめる。途中で答えが変わっても最後の答えで蒸留し、マージされた答えだけをルールの候補にする
        if task.to_distill and task.state == State.DONE:
            roles.append(Role.DISTILL)
        return roles

    def report(self) -> dict[str, Any]:
        """判断ストアの手入れの候補。report() は任意のメソッドで、無いか失敗したら空を返す（蒸留はそのまま走らせる）。"""
        report = getattr(self.decisions, "report", None)
        if report is None:
            return {}
        try:
            return report()
        except AdapterError as e:
            self._log({"report_error": str(e), "ts": _now()})
            return {}

    def _start_jobs(self, task: Task) -> None:
        """いま要るのに走っていないジョブを、実行アダプタの capacity の範囲で起動する。

        起動できなかったものは次の tick で起動する。落ちて消えたジョブもここで起動し直される。
        """
        for role in self._wanted(task):
            if role in task.jobs or self.executor.capacity() <= 0:
                continue
            job = Job(id=f"{task.id}.{role}.{task.next_job()}", role=role, task=task)
            report = self.report() if role == Role.DISTILL else None
            job.input = job_input(role, task, self._rules_for(role, task), self.agents.get(str(role)), report)
            record: dict[str, Any] = {"job_started": job.id, "task_id": task.id, "role": str(role)}
            if role == Role.DISTILL:
                # 蒸留が何を読んだか（クライアントの答えと report の候補の数）を残し、画面で見えるようにする
                record["questions"] = task.to_distill
                record["report"] = {k: len(v) for k, v in (report or {}).items() if k != "rules" and isinstance(v, list)}
                task.to_distill = []
            task.jobs[role] = job.id
            self.tasks.save(task)
            self.executor.start(job)
            self._log({**record, "ts": _now()})

    def _rules_for(self, role: Role, task: Task) -> list[Rule]:
        """ジョブに渡すルール。役の範囲のルールと範囲の無いルールを判断ストアから引く。

        蒸留はルールの手入れをするので全ルールを渡す。レビューには、判断や未決事項が引いたルールも足す
        （ほかの役の範囲のルールを引いていても、知らないルールとして差し戻さないため）。
        """
        if role == Role.DISTILL:
            return self.decisions.rules()
        rules = self.decisions.rules(scope=str(role))
        if role == Role.REVIEW:
            cited = {i for item in [*task.decisions, *task.questions] for i in item.rule_ids} - {r.id for r in rules}
            if cited:
                rules += [r for r in self.decisions.rules() if r.id in cited]
        return rules

    def poll(self) -> None:
        """走っているジョブの状態を実行アダプタに聞き、終わったものの結果でタスクを先へ進める。"""
        for listed in self.tasks.list():
            for role, job_id in listed.jobs.items():
                status = self.executor.status(job_id)
                if status.state in (RunState.RUNNING, RunState.BLOCKED):
                    continue
                with _retry_next_tick():
                    extra = self._collect(listed.id, role, job_id, status)
                    if extra is not None:
                        record = {"job": job_id, "task_id": listed.id, "role": role, "state": str(status.state), **extra}
                        self._log({**record, "usage": status.usage, "turns": status.turns, "ts": _now()})

    def _collect(self, task_id: str, role: str, job_id: str, status: RunStatus) -> dict[str, Any] | None:
        """終わったジョブの結果を受け取る。保存がぶつかったら何も残さず、次の tick で同じ結果をもう一度受け取る。

        受け取ったらジョブの終了の記録に足す項目（失敗の理由、蒸留が出した提案）を、もう受け取り済みなら None を返す。
        """
        # 前のジョブの処理でタスクが書き換わっているかもしれないので、読み直してから進める
        task = self.tasks.get(task_id)
        if task.jobs.get(role) != job_id:
            return None
        del task.jobs[role]
        for key, n in status.usage.items():
            task.usage[key] = task.usage.get(key, 0) + n
        if role != Role.DISTILL and ROLE_FOR_STATE.get(task.state) != role:
            self.tasks.save(task)  # 状態が先に進んだあとで届いた古い結果は捨てる
            return {}
        result = None
        if status.state == RunState.COMPLETED:
            try:
                result = parse(Role(role), status.result)
                self._check_depends(getattr(result, "tasks", []))
                self._check_retire(getattr(result, "retire", []))
            except ResultError as e:
                result, status.message = None, f"bad result from {role}: {e}"
        if result is None:
            self._job_failed(task, Role(role), status.message)
            return {"state": "failed", "message": status.message}  # 結果が読めなかったものも失敗として残す
        proposals = self._job_done(task, result)
        # 別件の起票は、このタスクの保存が通ってから行う。ぶつかって結果を受け取り直しても二重に起票しない
        for new in getattr(result, "tasks", []):
            self.create(
                new["title"],
                purpose=new.get("purpose", ""),
                questions=[{k: q[k] for k in QUESTION_FIELDS if k in q} for q in new.get("questions", [])],
                source="auto",
                priority=new.get("priority", 0),
                depends=new.get("depends", []),
            )
        return {"proposals": proposals} if isinstance(result, DistillResult) else {}

    def _check_depends(self, tasks: list[dict[str, Any]]) -> None:
        known = {t.id for t in self.tasks.list()}
        for new in tasks:
            if unknown := sorted(set(new.get("depends", [])) - known):
                raise ResultError(f"tasks depend on unknown tasks: {' '.join(unknown)}")

    def _check_retire(self, retire: list[dict[str, str]]) -> None:
        if retire and (unknown := sorted({r["rule_id"] for r in retire} - {r.id for r in self.decisions.rules()})):
            raise ResultError(f"retire unknown rules: {' '.join(unknown)}")

    def _job_failed(self, task: Task, role: Role, message: str) -> None:
        if role == Role.DISTILL:
            self.tasks.save(task)
            self.notifier.notify(task, f"{task.id} distill failed: {message}")
        else:
            self._fail(task, role, message)

    def _fail(self, task: Task, role: str, message: str) -> None:
        """タスクを failed にする。role は失敗した役（マージなら "merge"）。理由はタスクに持ち、algr show と reopen までの表示はそこから読む。"""
        task.failure = {"role": str(role), "message": message}
        self.emit("task.failed", task, role=str(role), message=message)

    def _job_done(self, task: Task, result: Any) -> list[str]:
        """結果でタスクを先へ進める。蒸留なら出した提案の ID を返す。"""
        if isinstance(result, IntakeResult):
            self._intake_done(task, result)
        elif isinstance(result, ImplementResult):
            self._implement_done(task, result)
        elif isinstance(result, ReviewResult):
            self._review_done(task, result)
        elif isinstance(result, DistillResult):
            # 同じ提案は二重に出ないので、ぶつかって受け取り直しても ID は変わらない
            proposals = [self.decisions.propose(r["rule"], source=r["source"], scope=r["scope"]) for r in result.rules]
            proposals += [self.decisions.propose_retire(r["rule_id"], r["reason"], source=r["source"]) for r in result.retire]
            self.tasks.save(task)
            return proposals
        return []

    def _ask(self, task: Task, questions: list[Question]) -> list[str]:
        """未決事項をクライアントに聞いたと記録する。同じ問いは二度聞かない。"""
        asked = []
        for q in questions:
            if not q.asked:
                q.asked = True
                self.decisions.escalate(q.id, q.text, "no_match")
                asked.append(q.id)
        return asked

    def _add_questions(self, task: Task, questions: list[dict[str, Any]]) -> list[Question]:
        return [
            task.add_question(q["text"], q.get("topic", ""), options=q.get("options", []), recommended=q.get("recommended"))
            for q in questions
        ]

    def _intake_done(self, task: Task, result: IntakeResult) -> None:
        """ルールで埋まった点を記録し、未決がゼロなら ready、残れば waiting-intake にしてクライアントに聞く。"""
        for fill in result.fills:
            q = task.question(fill.question_id)
            if not q.open:
                continue
            q.answer, q.by, q.rule_ids = fill.answer, "rule", fill.rule_ids
            self.decisions.apply(q.id, q.text, q.rule_ids)
        self._add_questions(task, result.questions)
        if task.open_questions():
            asked = self._ask(task, task.open_questions())
            self.emit("intake.asked", task, channel=str(task.channel), questions=asked)
        else:
            self.emit("task.ready", task)

    def _implement_done(self, task: Task, result: ImplementResult) -> None:
        if result.questions:
            # ルールで決まらない判断が出たら勝手に決めず、クライアントに聞く。答えがそろうと実装が走り直す（起票には戻さない）
            asked = self._ask(task, self._add_questions(task, result.questions))
            self.emit("implement.asked", task, channel=str(task.channel), questions=asked)
            return
        known = {q.id for q in task.questions}
        cited = {qid for d in result.decisions for qid in d.get("question_ids", [])}
        if unknown := sorted(cited - known):
            self._fail(task, Role.IMPLEMENT, f"decisions follow unknown questions: {' '.join(unknown)}")
            return
        # 判断の一覧は PR ごとに出し直す。前の回の判断は、新しい PR の一覧で置き換える
        task.pr, task.decisions, task.feedback = result.pr, [], []
        for d in result.decisions:
            decision = task.add_decision(
                d["text"], d["answer"], d.get("topic", ""), d.get("rule_ids"), d.get("question_ids")
            )
            # 未決事項の答えに従った判断は記録しない。答えは decide や apply で既に残っている
            if decision.rule_ids:
                self.decisions.apply(decision.id, decision.text, decision.rule_ids)
        self.emit("task.submitted", task, pr=task.pr)

    def _review_done(self, task: Task, result: ReviewResult) -> None:
        known = {q.id for q in task.questions} | {d.id for d in task.decisions}
        if unknown := sorted(set(result.objections) - known):
            self._fail(task, Role.REVIEW, f"objections to unknown decisions: {' '.join(unknown)}")
            return
        # クライアントが答えた未決事項は立場が決めたことなので、異議があっても蒸し返さない
        settled = {q.id for q in task.questions if q.by == "human"}
        objections = {did: why for did, why in result.objections.items() if did not in settled}
        if objections or result.questions:
            self._review_asks(task, objections, result.questions)  # 答えを持って走り直したレビューが、承認か修正依頼かを決める
        elif result.approved:
            self.emit("review.approved", task, pr=task.pr)
        elif result.changes:
            self._request_changes(task, result.changes)
        elif result.objections:
            self.emit("review.approved", task, pr=task.pr)  # 異議がすべてクライアントの決めたことへのものだった
        else:
            self._fail(task, Role.REVIEW, "review rejected without objections")

    def _request_changes(self, task: Task, changes: list[str]) -> None:
        if task.reworks >= self.max_rework:
            message = f"review asked for changes more than {self.max_rework} times: {'; '.join(changes)}"
            self._fail(task, Role.REVIEW, message)
            return
        task.reworks += 1
        task.feedback = list(changes)
        self.emit("review.changes_requested", task, changes=list(changes))

    def _review_asks(self, task: Task, objections: dict[str, str], questions: list[dict[str, Any]]) -> None:
        reopened = []
        for did in objections:
            # 異議のある判断は未決事項にしてクライアントに聞く。ルールに従った判断なら、ルールの書き直し候補にする
            if any(q.id == did for q in task.questions):
                q = task.question(did)
                if q.by == "rule":
                    self.decisions.reject(q.id, q.text, q.rule_ids)
                q.recommended, q.answer, q.by, q.rule_ids, q.asked = q.answer, None, None, [], False
            else:
                d = task.decision(did)
                if d.rule_ids:
                    self.decisions.reject(d.id, d.text, d.rule_ids)
                q = task.add_question(d.text, d.topic, recommended=d.answer)
                # 実装は走り直さないので、判断はこの問いの答えに従うものにする。答えから外れていれば、走り直したレビューが修正を頼む
                d.rule_ids, d.question_ids = [], [q.id]
            self._ask(task, [q])
            reopened.append(q.id)
        reopened += self._ask(task, self._add_questions(task, questions))
        self.emit("review.asked", task, channel=str(task.channel), questions=reopened, reasons=objections)

    # --- 既定の処理（イベントごと） -----------------------------------------

    def _on_task_created(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票エージェント

    def _notify_asked(self, task: Task, event: Event) -> None:
        # クライアントに聞くときは必ず知らせる。inline と queue の違いは聞き方（対話か、キューか）だけ
        if event.data["questions"]:
            role = ROLE_FOR_STATE[State(event.data["from"])]
            self.notifier.notify(task, f"{task.id} needs a decision from {role}: {' '.join(event.data['questions'])}")

    _on_intake_asked = _on_implement_asked = _on_review_asked = _notify_asked

    def _on_intake_answered(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票のやり直し

    def _on_task_ready(self, task: Task, event: Event) -> None:
        pass  # ボードに載った状態で配車を待つ

    def _on_task_started(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェント

    def _on_implement_answered(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェントが答えを持って走り直す

    def _on_review_changes_requested(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェントが指摘の分だけ直す

    def _on_merge_changes_requested(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェントが PJ の指摘の分だけ直す

    def _on_task_reopened(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票からやり直す

    def _on_task_submitted(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # レビューエージェント

    def _on_review_answered(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # レビューエージェントが答えを持って走り直す

    def _on_review_approved(self, task: Task, event: Event) -> None:
        self._merge(task)  # マージを頼む。入ったかどうかは tick でも見る

    def _on_task_done(self, task: Task, event: Event) -> None:
        # マージ先に入ったので、ルールに従った判断を hit として記録する。未決事項に従った判断は未決事項の側で記録される
        for item in [*task.questions, *task.decisions]:
            if item.rule_ids:
                self.decisions.decide(item.id, "hit", item.answer or "", predicted=item.answer)
        self.tasks.comment(task.id, f"merged: {task.pr}")
        self._start_jobs(task)  # 蒸留エージェント（クライアントが答えた未決事項があれば）

    def _blocks(self, task: Task) -> str:
        waiting = [t.id for t in self.tasks.list() if task.id in t.depends and t.state not in (State.DONE, State.CANCELED)]
        return f" (blocks {' '.join(waiting)})" if waiting else ""

    def _on_task_failed(self, task: Task, event: Event) -> None:
        # 依存しているタスクは待ち続ける。直すのは失敗したタスクの方なので、待っているタスクを添えて知らせる
        self.notifier.notify(task, f"{task.id} failed: {event.data.get('message', '')}{self._blocks(task)}")

    def _on_task_canceled(self, task: Task, event: Event) -> None:
        # 走っているジョブは止める（stop は任意）。結果が届いても、状態に合わないので捨てられる。
        # マージを頼んだ PR は閉じない（マージのアダプタに取り消しの口は無い）。依存しているタスクは待ち続けるので、外すか一緒にやめるかを決めてもらう
        stop = getattr(self.executor, "stop", None)
        for job_id in task.jobs.values() if stop else ():
            stop(job_id)
        blocks = self._blocks(task)
        if blocks:
            self.notifier.notify(task, f"{task.id} was canceled{blocks}")
