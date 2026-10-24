"""フローのコア。イベントを起こして状態を動かし、イベントごとの既定の処理と hook を呼ぶ。

外部には必ずアダプタ越しに触る。エージェントはすべて実行アダプタがジョブとして起動し、
コアはタスクの状態から「いま要るジョブ」を出して起動し、結果を受け取って状態を動かすだけ。
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from algr import flow
from algr.adapters import Conflict, DecisionStore, ExecutorAdapter, Notifier, RunState, RunStatus, TaskAdapter, TriggerAdapter
from algr.agents import DistillResult, ImplementResult, IntakeResult, ResultError, ReviewResult, job_input, parse
from algr.config import Config
from algr.model import Event, Job, Question, Role, State, Task

Hook = Callable[[Event, "Engine"], Any]

# 状態ごとに、その状態を先へ進めるエージェントの役
# トリガーが外から送れるイベント。人の操作（起票、回答、やり直し、マージ）と同じ入口を通る
EXTERNAL_EVENTS = ("task.created", "decision.resolved", "task.reopened", "task.done")

# エージェントが起票する別件の未決事項から受け取る項目
QUESTION_FIELDS = ("text", "topic", "options", "recommended")

ROLE_FOR_STATE = {State.INTAKE: Role.INTAKE, State.IN_PROGRESS: Role.IMPLEMENT, State.REVIEW: Role.REVIEW}


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
        notifier: Notifier,
        hooks: dict[str, list[Hook]] | None = None,
        max_parallel: int = 1,
        max_rework: int = 2,
        merge_gate: bool = False,
        event_log: Path | None = None,
    ) -> None:
        self.tasks = tasks
        self.trigger = trigger
        self.executor = executor
        self.decisions = decisions
        self.notifier = notifier
        self.hooks = hooks or {}
        self.max_parallel = max_parallel
        self.max_rework = max_rework
        self.merge_gate = merge_gate
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
            notifier=config.adapter("notify"),
            hooks=config.hooks(),
            max_parallel=config.max_parallel,
            max_rework=config.max_rework,
            merge_gate=config.merge_gate,
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
        """hook は既定の処理のあとに呼ぶ。状態はもう動いているので、hook が落ちてもフローは止めずに人に知らせる。"""
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

    # --- 人やトリガーからの入口 -------------------------------------------

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
        """意図を受け取り、タスクを起票に流す。questions は人が最初から分かっている未決事項。"""
        for dep in depends or []:
            self.tasks.get(dep)  # 無いタスクには依存できない
        task = Task(id="", title=title, purpose=purpose, source=source, priority=priority, depends=list(depends or []))
        task = self.tasks.create(task)
        for q in questions or []:
            task.add_question(**q)
        self.emit("task.created", task, source=source)
        return self.tasks.get(task.id)

    def resolve(self, task_id: str, answers: dict[str, str]) -> Task:
        """人が未決事項に答える。答えると必ず起票に戻り、そこから主経路に合流する。"""
        task = self.tasks.get(task_id)
        if task.state != State.NEEDS_DECISION:
            raise flow.TransitionError(f"{task_id} is {task.state}, not waiting for a decision")
        for qid, answer in answers.items():
            q = task.question(qid)
            if not q.open:
                raise flow.TransitionError(f"{qid} is already decided")
            q.answer, q.by = answer, "human"
            self.decisions.decide(q.id, "none", answer)
            task.to_distill.append(q.id)
        self.emit("decision.resolved", task, questions=list(answers))
        return self.tasks.get(task_id)

    def reopen(self, task_id: str, note: str = "") -> Task:
        """人が failed のタスクを起票に戻す。修正依頼の回数は数え直す。"""
        task = self.tasks.get(task_id)
        if task.state != State.FAILED:
            raise flow.TransitionError(f"{task_id} is {task.state}, not failed")
        task.reworks, task.approved = 0, False
        self.emit("task.reopened", task, note=note)
        return self.tasks.get(task_id)

    def merge(self, task_id: str) -> Task:
        """マージのゲートで、人が承認済みのタスクをマージする（マージされたと記録する）。"""
        task = self.tasks.get(task_id)
        if task.state != State.REVIEW or not task.approved:
            raise flow.TransitionError(f"{task_id} is not approved and waiting for merge")
        task.approved = False
        self.emit("task.done", task, pr=task.pr, merged_by="human")
        return self.tasks.get(task_id)

    def tick(self) -> list[Event]:
        """1 周分：トリガーのイベントを取り込み、ジョブの結果を受け取り、ready を配車し、要るジョブを起動する。"""
        start = len(self.events)
        for event in self.trigger.poll():
            self.handle_external(event)
        self.poll()
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

    def handle_external(self, event: Event) -> None:
        if event.name == "task.created":
            self.create(**event.data)
        elif event.name == "decision.resolved":
            self.resolve(event.task_id, event.data["answers"])
        elif event.name == "task.reopened":
            self.reopen(event.task_id, event.data.get("note", ""))
        elif event.name == "task.done":
            self.merge(event.task_id)
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

    # --- ジョブ ------------------------------------------------------------

    def _wanted(self, task: Task) -> list[Role]:
        roles = [ROLE_FOR_STATE[task.state]] if task.state in ROLE_FOR_STATE and not task.approved else []
        if task.to_distill:
            roles.append(Role.DISTILL)
        return roles

    def _start_jobs(self, task: Task) -> None:
        """いま要るのに走っていないジョブを、実行アダプタの capacity の範囲で起動する。

        起動できなかったものは次の tick で起動する。落ちて消えたジョブもここで起動し直される。
        """
        for role in self._wanted(task):
            if role in task.jobs or self.executor.capacity() <= 0:
                continue
            job = Job(id=f"{task.id}.{role}.{task.next_job()}", role=role, task=task)
            job.input = job_input(role, task, self.decisions.rules())
            if role == Role.DISTILL:
                task.to_distill = []
            task.jobs[role] = job.id
            self.tasks.save(task)
            self.executor.start(job)

    def poll(self) -> None:
        """走っているジョブの状態を実行アダプタに聞き、終わったものの結果でタスクを先へ進める。"""
        for listed in self.tasks.list():
            for role, job_id in listed.jobs.items():
                status = self.executor.status(job_id)
                if status.state in (RunState.RUNNING, RunState.BLOCKED):
                    continue
                with _retry_next_tick():
                    self._collect(listed.id, role, job_id, status)

    def _collect(self, task_id: str, role: str, job_id: str, status: RunStatus) -> None:
        """終わったジョブの結果を受け取る。保存がぶつかったら何も残さず、次の tick で同じ結果をもう一度受け取る。"""
        # 前のジョブの処理でタスクが書き換わっているかもしれないので、読み直してから進める
        task = self.tasks.get(task_id)
        if task.jobs.get(role) != job_id:
            return
        del task.jobs[role]
        if role != Role.DISTILL and ROLE_FOR_STATE.get(task.state) != role:
            self.tasks.save(task)  # 状態が先に進んだあとで届いた古い結果は捨てる
            return
        result = None
        if status.state == RunState.COMPLETED:
            try:
                result = parse(Role(role), status.result)
                self._check_depends(getattr(result, "tasks", []))
            except ResultError as e:
                result, status.message = None, f"bad result from {role}: {e}"
        if result is None:
            self._job_failed(task, Role(role), status.message)
        else:
            self._job_done(task, result)
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

    def _check_depends(self, tasks: list[dict[str, Any]]) -> None:
        known = {t.id for t in self.tasks.list()}
        for new in tasks:
            if unknown := sorted(set(new.get("depends", [])) - known):
                raise ResultError(f"tasks depend on unknown tasks: {' '.join(unknown)}")

    def _job_failed(self, task: Task, role: Role, message: str) -> None:
        if role == Role.DISTILL:
            self.tasks.save(task)
            self.notifier.notify(task, f"{task.id} distill failed: {message}")
        else:
            self.emit("task.failed", task, role=str(role), message=message)

    def _job_done(self, task: Task, result: Any) -> None:
        if isinstance(result, IntakeResult):
            self._intake_done(task, result)
        elif isinstance(result, ImplementResult):
            self._implement_done(task, result)
        elif isinstance(result, ReviewResult):
            self._review_done(task, result)
        elif isinstance(result, DistillResult):
            for r in result.rules:
                self.decisions.propose(r["rule"], source=r["source"])
            self.tasks.save(task)

    def _ask(self, task: Task, questions: list[Question]) -> list[str]:
        """未決事項を人に上げたと記録する。同じ問いは二度上げない。"""
        asked = []
        for q in questions:
            if not q.asked:
                q.asked = True
                self.decisions.escalate(q.id, q.text, "no_match")
                asked.append(q.id)
        return asked

    def _intake_done(self, task: Task, result: IntakeResult) -> None:
        """ルールで埋まった点を記録し、未決がゼロなら ready、残れば needs-decision にする。"""
        for fill in result.fills:
            q = task.question(fill.question_id)
            if not q.open:
                continue
            q.answer, q.by, q.rule_ids = fill.answer, "rule", fill.rule_ids
            self.decisions.apply(q.id, q.text, q.rule_ids)
        for new in result.questions:
            task.add_question(
                new["text"], new.get("topic", ""), options=new.get("options", []), recommended=new.get("recommended")
            )
        if task.open_questions():
            asked = self._ask(task, task.open_questions())
            self.emit("decision.requested", task, channel=str(task.channel), questions=asked)
        else:
            self.emit("task.ready", task)

    def _implement_done(self, task: Task, result: ImplementResult) -> None:
        if result.returned is not None:
            # 想定外の判断が出たら勝手に決めず、起票に差し戻す。起票エージェントがルールを当て、埋まらなければ人に聞く
            r = result.returned
            q = task.add_question(r["text"], r.get("topic", ""), recommended=r.get("recommended"))
            self.emit("task.returned", task, questions=[q.id])
            return
        # 判断の一覧は PR ごとに出し直す。前の回の判断は、新しい PR の一覧で置き換える
        task.pr, task.decisions, task.feedback = result.pr, [], []
        for d in result.decisions:
            decision = task.add_decision(d["text"], d["answer"], d.get("topic", ""), d.get("rule_ids"))
            if decision.rule_ids:
                self.decisions.apply(decision.id, decision.text, decision.rule_ids)
        self.emit("task.submitted", task, pr=task.pr)

    def _review_done(self, task: Task, result: ReviewResult) -> None:
        known = {q.id for q in task.questions} | {d.id for d in task.decisions}
        if unknown := sorted(set(result.objections) - known):
            self.emit("task.failed", task, role=str(Role.REVIEW), message=f"objections to unknown decisions: {' '.join(unknown)}")
            return
        # 人が答えた未決事項は人のゲートを通っているので、異議があっても蒸し返さない
        settled = {q.id for q in task.questions if q.by == "human"}
        objections = {did: why for did, why in result.objections.items() if did not in settled}
        if result.approved:
            self._approved(task)
        elif objections:
            self._escalate(task, objections)
        elif result.changes:
            self._request_changes(task, result.changes)
        elif result.objections:
            self._approved(task)  # 異議がすべて人の決めたことへのものだった
        else:
            self.emit("task.failed", task, role=str(Role.REVIEW), message="review rejected without objections")

    def _approved(self, task: Task) -> None:
        if not self.merge_gate:
            self.emit("task.done", task, pr=task.pr, merged_by="auto")
            return
        task.approved = True
        self.tasks.save(task)
        self.notifier.notify(task, f"{task.id} is approved and waiting for merge: {task.pr}")

    def _request_changes(self, task: Task, changes: list[str]) -> None:
        if task.reworks >= self.max_rework:
            message = f"review asked for changes more than {self.max_rework} times: {'; '.join(changes)}"
            self.emit("task.failed", task, role=str(Role.REVIEW), message=message)
            return
        task.reworks += 1
        task.feedback = list(changes)
        self.emit("review.changes_requested", task, changes=list(changes))

    def _escalate(self, task: Task, objections: dict[str, str]) -> None:
        reopened = []
        for did in objections:
            # 異議のある判断は未決事項として起票に差し戻す。ルールに従った判断なら、ルールの書き直し候補にする
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
            self._ask(task, [q])
            reopened.append(q.id)
        self.emit("review.escalated", task, questions=reopened, reasons=objections)

    # --- 既定の処理（イベントごと） -----------------------------------------

    def _on_task_created(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票エージェント

    def _on_decision_requested(self, task: Task, event: Event) -> None:
        # 人に聞くときは必ず知らせる。inline と queue の違いは聞き方（対話か、キューか）だけ
        if event.data["questions"]:
            self.notifier.notify(task, f"{task.id} needs a decision: {' '.join(event.data['questions'])}")

    def _on_decision_resolved(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票のやり直しと、蒸留エージェント

    def _on_task_ready(self, task: Task, event: Event) -> None:
        pass  # ボードに載った状態で配車を待つ

    def _on_task_started(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェント

    def _on_task_returned(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票エージェントがルールを当て直す

    def _on_review_changes_requested(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 実装エージェントが指摘の分だけ直す

    def _on_task_reopened(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # 起票からやり直す

    def _on_task_submitted(self, task: Task, event: Event) -> None:
        self._start_jobs(task)  # レビューエージェント

    def _on_review_escalated(self, task: Task, event: Event) -> None:
        self.notifier.notify(task, f"{task.id} was escalated in review: {' '.join(event.data['questions'])}")

    def _on_task_done(self, task: Task, event: Event) -> None:
        # マージされたので、ルールに従った判断を hit として記録する
        for item in [*task.questions, *task.decisions]:
            if item.by == "rule":
                self.decisions.decide(item.id, "hit", item.answer or "", predicted=item.answer)
        self.tasks.comment(task.id, f"merged: {task.pr}")

    def _on_task_failed(self, task: Task, event: Event) -> None:
        # 依存しているタスクは待ち続ける。直すのは失敗したタスクの方なので、待っているタスクを添えて人に上げる
        waiting = [t.id for t in self.tasks.list() if task.id in t.depends and t.state != State.DONE]
        blocks = f" (blocks {' '.join(waiting)})" if waiting else ""
        self.notifier.notify(task, f"{task.id} failed: {event.data.get('message', '')}{blocks}")
