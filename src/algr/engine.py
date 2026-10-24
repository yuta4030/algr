"""フローのコア。イベントを起こして状態を動かし、イベントごとの既定の処理と hook を呼ぶ。

外部には必ずアダプタ越しに触る。判断の中身はエージェントが決め、ここは状態と記録だけを扱う。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from algr import flow
from algr.adapters import DecisionStore, ExecutorAdapter, Notifier, RunState, TaskAdapter, TriggerAdapter
from algr.agents import DistillAgent, IntakeAgent, ReviewAgent
from algr.config import Config
from algr.model import Channel, Event, Question, State, Task

Hook = Callable[[Event, "Engine"], Any]


class Engine:
    def __init__(
        self,
        *,
        tasks: TaskAdapter,
        trigger: TriggerAdapter,
        executor: ExecutorAdapter,
        decisions: DecisionStore,
        notifier: Notifier,
        intake: IntakeAgent,
        review: ReviewAgent,
        distill: DistillAgent,
        hooks: dict[str, list[Hook]] | None = None,
        max_parallel: int = 1,
        event_log: Path | None = None,
    ) -> None:
        self.tasks = tasks
        self.trigger = trigger
        self.executor = executor
        self.decisions = decisions
        self.notifier = notifier
        self.intake_agent = intake
        self.review_agent = review
        self.distill_agent = distill
        self.hooks = hooks or {}
        self.max_parallel = max_parallel
        self.event_log = event_log
        self.events: list[Event] = []
        self._pending: deque[Event] = deque()
        self._draining = False
        for name in self.hooks:
            if name not in flow.EVENTS:
                raise flow.TransitionError(f"hook for unknown event: {name}")

    @classmethod
    def from_config(cls, config: Config) -> Engine:
        return cls(
            tasks=config.adapter("task"),
            trigger=config.adapter("trigger"),
            executor=config.adapter("executor"),
            decisions=config.adapter("decisions"),
            notifier=config.adapter("notify"),
            intake=config.agent("intake"),
            review=config.agent("review"),
            distill=config.agent("distill"),
            hooks=config.hooks(),
            max_parallel=config.max_parallel,
            event_log=config.ctx.path(".algr/state/events.jsonl"),
        )

    # --- イベント ---------------------------------------------------------

    def emit(self, name: str, task: Task, **data: Any) -> Event:
        """遷移を確かめて状態を動かし、保存してからイベントを積む。処理は積んだ順に 1 つずつ行う。"""
        task.state = flow.check(name, task)
        self.tasks.save(task)
        event = Event(name, task.id, data)
        self.events.append(event)
        if self.event_log:
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
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
                handler(self.tasks.get(event.task_id), event)
                for hook in self.hooks.get(event.name, []):
                    hook(event, self)
        finally:
            self._draining = False

    # --- 人やトリガーからの入口 -------------------------------------------

    def create(
        self,
        title: str,
        *,
        purpose: str = "",
        questions: list[dict[str, Any]] | None = None,
        source: str = "human",
        priority: int = 0,
        budget: int | None = None,
    ) -> Task:
        """意図を受け取り、タスクを起票に流す。questions は意図から洗い出した未決事項。"""
        task = self.tasks.create(Task(id="", title=title, purpose=purpose, source=source, priority=priority, budget=budget))
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
        self.emit("decision.resolved", task, answered=list(answers))
        return self.tasks.get(task_id)

    def tick(self) -> list[Event]:
        """1 周分：トリガーのイベントを取り込み、走っているタスクを見て、ready のタスクを配車する。"""
        start = len(self.events)
        for event in self.trigger.poll():
            self.handle_external(event)
        self.poll()
        self.dispatch()
        return self.events[start:]

    def handle_external(self, event: Event) -> None:
        if event.name == "task.created":
            self.create(**event.data)
        elif event.name == "decision.resolved":
            self.resolve(event.task_id, event.data["answers"])
        else:
            raise flow.TransitionError(f"triggers may only send task.created or decision.resolved, got {event.name}")

    def dispatch(self) -> list[Task]:
        """ready のタスクを優先度順に、同時実行数と実行アダプタの capacity の範囲で始める。"""
        started = []
        ready = sorted(self.tasks.list(State.READY), key=lambda t: -t.priority)
        for task in ready:
            if len(self.tasks.list(State.IN_PROGRESS)) >= self.max_parallel or self.executor.capacity() <= 0:
                break
            self.emit("task.started", task)
            started.append(task)
        return started

    def poll(self) -> None:
        """走っているタスクの状態を実行アダプタに聞き、終わったものを先へ進める。"""
        for task in self.tasks.list(State.IN_PROGRESS):
            status = self.executor.status(task)
            if status.state == RunState.RUNNING:
                continue
            if status.state == RunState.COMPLETED:
                task.pr = status.result.get("pr")
                task.result = status.result
                for d in status.result.get("decisions", []):
                    rule_ids = list(d.get("rule_ids", []))
                    q = task.add_question(
                        d["text"], d.get("topic", ""), answer=d["answer"], by="rule" if rule_ids else "agent", rule_ids=rule_ids
                    )
                    if rule_ids:
                        self.decisions.apply(q.id, q.text, rule_ids)
                self.emit("task.submitted", task, pr=task.pr)
            elif status.state == RunState.BLOCKED:
                # 想定外の判断が出たら勝手に決めず、起票に差し戻す
                returned = status.result["returned"]
                q = task.add_question(returned["text"], returned.get("topic", ""), recommended=returned.get("recommended"))
                self.emit("task.returned", task, question=q.id)
            else:
                self.emit("task.failed", task, message=status.message)

    # --- 起票 ------------------------------------------------------------

    def _intake(self, task: Task) -> None:
        """ルールを当てて決まる点を埋め、未決がゼロなら ready、残れば needs-decision にする。"""
        for fill in self.intake_agent.intake(task, self.decisions.rules()):
            q = task.question(fill.question_id)
            q.answer, q.by, q.rule_ids = fill.answer, "rule", list(fill.rule_ids)
            self.decisions.apply(q.id, q.text, q.rule_ids)
        if task.open_questions():
            asked = [q for q in task.open_questions() if not q.asked]
            self._escalate(task, asked)
            self.emit("decision.requested", task, channel=str(task.channel), questions=[q.id for q in asked])
        else:
            self.emit("task.ready", task)

    def _escalate(self, task: Task, questions: list[Question]) -> None:
        for q in questions:
            q.asked = True
            self.decisions.escalate(q.id, q.text, "no_match")
        self.tasks.save(task)

    # --- 既定の処理（イベントごと） -----------------------------------------

    def _on_task_created(self, task: Task, event: Event) -> None:
        self._intake(task)

    def _on_decision_requested(self, task: Task, event: Event) -> None:
        # 同期の起票はその場で聞くので通知しない。非同期だけ判断キューに積んで知らせる
        if event.data["channel"] == Channel.QUEUE and event.data["questions"]:
            self.notifier.notify(task, f"{task.id} needs a decision: {' '.join(event.data['questions'])}")

    def _on_decision_resolved(self, task: Task, event: Event) -> None:
        # 判断を記録し、ルール化を提案し、起票をやり直す
        for qid in event.data["answered"]:
            q = task.question(qid)
            self.decisions.decide(q.id, "none", q.answer or "", predicted=q.recommended)
            rule = self.distill_agent.distill(task, q)
            if rule:
                self.decisions.propose(rule, source=q.id)
        self._intake(task)

    def _on_task_ready(self, task: Task, event: Event) -> None:
        pass  # ボードに載った状態で配車を待つ

    def _on_task_started(self, task: Task, event: Event) -> None:
        self.executor.start(task)

    def _on_task_returned(self, task: Task, event: Event) -> None:
        self._escalate(task, [task.question(event.data["question"])])
        self.notifier.notify(task, f"{task.id} was returned during implementation: {event.data['question']}")

    def _on_task_submitted(self, task: Task, event: Event) -> None:
        review = self.review_agent.review(task, self.decisions.rules())
        if review.approved:
            self.emit("task.done", task, pr=task.pr)
            return
        if not review.objections:
            self.emit("task.failed", task, message="review rejected without objections")
            return
        for qid in review.objections:
            q = task.question(qid)
            q.recommended, q.answer, q.by, q.rule_ids, q.asked = q.answer, None, None, [], False
        self.emit("review.escalated", task, questions=list(review.objections), reasons=review.objections)

    def _on_review_escalated(self, task: Task, event: Event) -> None:
        self._escalate(task, [task.question(qid) for qid in event.data["questions"]])
        self.notifier.notify(task, f"{task.id} was escalated in review: {' '.join(event.data['questions'])}")

    def _on_task_done(self, task: Task, event: Event) -> None:
        # マージされたので、ルールに従った判断を hit として記録する
        for q in task.questions:
            if q.by == "rule":
                self.decisions.decide(q.id, "hit", q.answer or "", predicted=q.answer)
        self.tasks.comment(task.id, f"merged: {task.pr}")

    def _on_task_failed(self, task: Task, event: Event) -> None:
        self.notifier.notify(task, f"{task.id} failed: {event.data.get('message', '')}")
