"""タスク、未決事項、判断、イベントのデータ型。保存はアダプタが行い、ここは形だけを持つ。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class State(StrEnum):
    INTAKE = "intake"
    NEEDS_DECISION = "needs-decision"
    READY = "ready"
    IN_PROGRESS = "in-progress"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"


class Channel(StrEnum):
    """未決事項をどこで聞くか。人が作ったタスクは同期、自動で生まれたタスクは非同期。"""

    INLINE = "inline"
    QUEUE = "queue"


class Role(StrEnum):
    """実行アダプタが起動するエージェントの役。"""

    INTAKE = "intake"
    IMPLEMENT = "implement"
    REVIEW = "review"
    DISTILL = "distill"


@dataclass
class Question:
    """起票で埋める未決事項 1 つ。answer が None のあいだは未決。"""

    id: str
    text: str
    topic: str = ""
    options: list[str] = field(default_factory=list)
    recommended: str | None = None
    answer: str | None = None
    by: str | None = None  # "rule"（ルールで埋まった） | "human"（人が答えた）
    rule_ids: list[str] = field(default_factory=list)
    asked: bool = False  # 人に上げ済みか。同じ問いを二度上げない

    @property
    def open(self) -> bool:
        return self.answer is None


@dataclass
class Decision:
    """実装エージェントが実装中に下した判断 1 つ。PR に載せる判断の一覧になる。"""

    id: str
    text: str
    answer: str
    topic: str = ""
    rule_ids: list[str] = field(default_factory=list)

    @property
    def by(self) -> str:
        """ルールに従ったか、根拠なくエージェントが決めたか。"""
        return "rule" if self.rule_ids else "agent"


@dataclass
class Task:
    id: str
    title: str
    state: State = State.INTAKE
    purpose: str = ""
    source: str = "human"  # "human" | "auto"
    priority: int = 0  # 大きいほど先に配車する
    budget: int | None = None  # トークン予算。フェーズ0では持つだけ
    questions: list[Question] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    pr: str | None = None
    seq: int = 0  # 決断 ID の採番。未決事項と判断で共有し、消しても番号は使い回さない
    jobs: dict[str, str] = field(default_factory=dict)  # 役 → 走っているジョブの ID
    runs: int = 0  # ジョブの採番。やり直しても同じ ID のジョブにならないようにする
    to_distill: list[str] = field(default_factory=list)  # 蒸留を待つ、人が答えた未決事項の ID

    @property
    def channel(self) -> Channel:
        return Channel.INLINE if self.source == "human" else Channel.QUEUE

    def open_questions(self) -> list[Question]:
        return [q for q in self.questions if q.open]

    def question(self, qid: str) -> Question:
        for q in self.questions:
            if q.id == qid:
                return q
        raise KeyError(f"{self.id} has no question {qid}")

    def decision(self, did: str) -> Decision:
        for d in self.decisions:
            if d.id == did:
                return d
        raise KeyError(f"{self.id} has no decision {did}")

    def next_id(self) -> str:
        """決断 ID はタスク単位で振る（例：I123-1）。k2down の記録とタスクがつながる。"""
        self.seq += 1
        return f"{self.id}-{self.seq}"

    def next_job(self) -> int:
        self.runs += 1
        return self.runs

    def add_question(self, text: str, topic: str = "", **fields: Any) -> Question:
        q = Question(id=self.next_id(), text=text, topic=topic, **fields)
        self.questions.append(q)
        return q

    def add_decision(self, text: str, answer: str, topic: str = "", rule_ids: list[str] | None = None) -> Decision:
        d = Decision(id=self.next_id(), text=text, answer=answer, topic=topic, rule_ids=list(rule_ids or []))
        self.decisions.append(d)
        return d

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = str(self.state)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        data = dict(data)
        data["state"] = State(data.get("state", State.INTAKE))
        data["questions"] = [Question(**q) for q in data.get("questions", [])]
        data["decisions"] = [Decision(**d) for d in data.get("decisions", [])]
        return cls(**data)


@dataclass
class Job:
    """実行アダプタに渡す仕事 1 つ。エージェントは input を読み、結果を返して終わる。"""

    id: str
    role: Role
    task: Task
    input: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "role": str(self.role), "task": self.task.to_dict(), "input": self.input}


@dataclass
class Event:
    name: str
    task_id: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
