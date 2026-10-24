"""起票・レビュー・蒸留のエージェントの IF。

判断の中身はエージェント（ホスト側の AI）が決め、コアはその結果で状態を動かすだけ。
実装エージェントは実行アダプタが起動する。プロンプトとスキルはフェーズ1でコアに入れる。
ここにあるフェイクは、ルールの本文に論点（topic）が含まれるかだけで判断する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from algr.adapters import Rule
from algr.model import Question, Task


@dataclass
class Fill:
    """ルールで埋まった判断 1 つ。"""

    question_id: str
    answer: str
    rule_ids: list[str]


@dataclass
class Review:
    approved: bool
    # 異議：判断 ID → 理由。異議のある判断は未決に戻し、起票に差し戻す
    objections: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class IntakeAgent(Protocol):
    def intake(self, task: Task, rules: list[Rule]) -> list[Fill]:
        """未決事項にルールを当て、決まった点を返す。決まらない点は返さない。"""
        ...


@runtime_checkable
class ReviewAgent(Protocol):
    def review(self, task: Task, rules: list[Rule]) -> Review: ...


@runtime_checkable
class DistillAgent(Protocol):
    def distill(self, task: Task, question: Question) -> str | None:
        """人の判断から 1 行ルールを作る。ルールにしないなら None。"""
        ...


RULE_SEPARATOR = ": "


class FakeIntake:
    """「<topic>: <answer>」の形のルールを、同じ topic の未決事項に当てる。"""

    def intake(self, task: Task, rules: list[Rule]) -> list[Fill]:
        fills = []
        for q in task.open_questions():
            for rule in rules:
                topic, sep, answer = rule.text.partition(RULE_SEPARATOR)
                if sep and q.topic and topic == q.topic:
                    fills.append(Fill(q.id, answer, [rule.id]))
                    break
        return fills


class FakeReview:
    """PR があり、すべての判断が実在するルールか人の判断に基づいていれば通す。"""

    def review(self, task: Task, rules: list[Rule]) -> Review:
        known = {r.id for r in rules}
        objections = {}
        for q in task.questions:
            if q.by == "agent":
                objections[q.id] = "decided without a rule"
            elif q.by == "rule" and not set(q.rule_ids) <= known:
                objections[q.id] = "cites unknown rules"
        return Review(approved=bool(task.pr) and not objections, objections=objections)


class FakeDistill:
    """topic のある判断を「<topic>: <answer>」のルールにする。"""

    def distill(self, task: Task, question: Question) -> str | None:
        if not question.topic or question.answer is None:
            return None
        return f"{question.topic}{RULE_SEPARATOR}{question.answer}"
