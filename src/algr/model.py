"""タスク、未決事項、イベントのデータ型。保存はアダプタが行い、ここは形だけを持つ。"""

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


@dataclass
class Question:
    """起票で埋める判断 1 つ。answer が None のあいだは未決。"""

    id: str
    text: str
    topic: str = ""
    options: list[str] = field(default_factory=list)
    recommended: str | None = None
    answer: str | None = None
    by: str | None = None  # "rule"（ルールで決めた） | "human"（人が決めた） | "agent"（根拠なくエージェントが決めた）
    rule_ids: list[str] = field(default_factory=list)
    asked: bool = False  # 人に上げ済みか。同じ問いを二度上げない

    @property
    def open(self) -> bool:
        return self.answer is None


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
    pr: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

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

    def add_question(self, text: str, topic: str = "", **fields: Any) -> Question:
        """決断 ID はタスク単位で振る（例：I123-1）。k2down の記録とタスクがつながる。"""
        q = Question(id=f"{self.id}-{len(self.questions) + 1}", text=text, topic=topic, **fields)
        self.questions.append(q)
        return q

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = str(self.state)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        data = dict(data)
        data["state"] = State(data.get("state", State.INTAKE))
        data["questions"] = [Question(**q) for q in data.get("questions", [])]
        return cls(**data)


@dataclass
class Event:
    name: str
    task_id: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
