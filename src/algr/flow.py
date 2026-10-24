"""状態遷移とイベント、遷移ごとの不変条件。

タスクは 7 つの状態を移り、遷移ごとにイベントが 1 つ出る。そのイベントがそのまま hook の差し込み口になる。
人が関わるのは needs-decision と failed、それにマージのゲートを置いたときの承認済みの review だけ。
"""

from __future__ import annotations

from collections.abc import Callable

from algr.model import State, Task

S = State

# イベント名 → (遷移元の集合, 遷移先)。task.created は新規タスクなので遷移元を持たない。
TRANSITIONS: dict[str, tuple[frozenset[State], State]] = {
    "task.created": (frozenset(), S.INTAKE),
    "decision.requested": (frozenset({S.INTAKE}), S.NEEDS_DECISION),
    "decision.resolved": (frozenset({S.NEEDS_DECISION}), S.INTAKE),
    "task.ready": (frozenset({S.INTAKE}), S.READY),
    "task.started": (frozenset({S.READY}), S.IN_PROGRESS),
    "task.returned": (frozenset({S.IN_PROGRESS}), S.INTAKE),
    "task.submitted": (frozenset({S.IN_PROGRESS}), S.REVIEW),
    "review.changes_requested": (frozenset({S.REVIEW}), S.IN_PROGRESS),
    "review.escalated": (frozenset({S.REVIEW}), S.NEEDS_DECISION),
    "task.done": (frozenset({S.REVIEW}), S.DONE),
    "task.failed": (frozenset({S.INTAKE, S.IN_PROGRESS, S.REVIEW}), S.FAILED),
    "task.reopened": (frozenset({S.FAILED}), S.INTAKE),
}

EVENTS = tuple(TRANSITIONS)

# イベントごとのペイロード（Event.data）のキー。hook はこれを当てにしてよい。
# どのイベントにも遷移元の状態 "from" が付く（task.created は ""）。決断 ID の並びは必ず "questions" で渡す。
EVENT_DATA: dict[str, tuple[str, ...]] = {
    "task.created": ("source",),
    "decision.requested": ("channel", "questions"),
    "decision.resolved": ("questions",),
    "task.ready": (),
    "task.started": (),
    "task.returned": ("questions",),
    "task.submitted": ("pr",),
    "review.changes_requested": ("changes",),
    "review.escalated": ("questions", "reasons"),
    "task.done": ("pr", "merged_by"),
    "task.failed": ("role", "message"),
    "task.reopened": ("note",),
}

# hook の差し込み口。イベント名のほかに、すべてのイベントを受ける "*" を使える
HOOK_POINTS = (*EVENTS, "*")


class TransitionError(Exception):
    pass


def _no_open_questions(task: Task) -> str | None:
    if task.open_questions():
        ids = " ".join(q.id for q in task.open_questions())
        return f"{task.id} still has open questions: {ids}"
    return None


def _has_open_questions(task: Task) -> str | None:
    if not task.open_questions():
        return f"{task.id} has no open question to ask"
    return None


def _has_pr(task: Task) -> str | None:
    if not task.pr:
        return f"{task.id} was submitted without a PR"
    return None


def _fills_have_grounds(task: Task) -> str | None:
    """判断には根拠を付ける：ルールで埋まった未決事項には、従ったルールが必ずある。"""
    for q in task.questions:
        if q.by == "rule" and not q.rule_ids:
            return f"{task.id} filled {q.id} by rule without citing one"
    return None


INVARIANTS: dict[str, tuple[Callable[[Task], str | None], ...]] = {
    "decision.requested": (_has_open_questions,),
    "task.ready": (_no_open_questions, _fills_have_grounds),
    "task.returned": (_has_open_questions,),
    "task.submitted": (_has_pr,),
    "review.escalated": (_has_open_questions,),
    "review.changes_requested": (_has_pr,),
    "task.done": (_has_pr, _no_open_questions),
}


def check(event: str, task: Task) -> State:
    """event を task に起こしてよいかを確かめ、遷移先を返す。task は書き換えない。"""
    if event not in TRANSITIONS:
        raise TransitionError(f"unknown event: {event}")
    sources, target = TRANSITIONS[event]
    if sources and task.state not in sources:
        raise TransitionError(f"{event} is not allowed from {task.state} ({task.id})")
    for invariant in INVARIANTS.get(event, ()):
        problem = invariant(task)
        if problem:
            raise TransitionError(f"{event}: {problem}")
    return target


def check_data(event: str, data: dict) -> None:
    """event のペイロードが EVENT_DATA の形どおりかを確かめる。"""
    want = {"from", *EVENT_DATA[event]}
    if set(data) != want:
        raise TransitionError(f"{event} carries {sorted(data)}, expected {sorted(want)}")
