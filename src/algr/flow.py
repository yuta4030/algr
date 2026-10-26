"""状態遷移とイベント、遷移ごとの不変条件。

タスクは 8 つの状態を移り、遷移ごとにイベントが 1 つ出る。そのイベントがそのまま hook の差し込み口になる。
人が関わるのは needs-decision と failed、それにマージのゲートを置いたときの approved だけ。
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
    "review.approved": (frozenset({S.REVIEW}), S.APPROVED),
    "task.done": (frozenset({S.REVIEW, S.APPROVED}), S.DONE),
    "task.failed": (frozenset({S.INTAKE, S.IN_PROGRESS, S.REVIEW, S.APPROVED}), S.FAILED),
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
    "review.approved": ("pr",),
    "task.done": ("pr", "merged_by"),
    "task.failed": ("role", "message"),
    "task.reopened": ("note",),
}

# 状態とイベントの説明。README の表と algr ui のヘルプはここから作るか、ここと突き合わせる（tests/test_help.py）
STATE_HELP: dict[State, str] = {
    S.INTAKE: "起票。未決事項をルールで埋め、埋まらないものは人に上げる",
    S.NEEDS_DECISION: "人の答え待ち。答えると起票に戻る",
    S.READY: "未決事項が無くなり、配車を待つ。依存先がすべて done になるまで配車しない",
    S.IN_PROGRESS: "実装エージェントが実装し、PR を出す",
    S.REVIEW: "レビューエージェントが PR と判断を確かめる",
    S.APPROVED: "レビューが通り、人のマージを待つ（flow.merge = \"human\" のときだけ）",
    S.DONE: "マージ済み。人が答えた未決事項があれば蒸留エージェントが走る",
    S.FAILED: "失敗。理由はタスクの failure に残り、人が reopen すると起票に戻る",
}

# イベントごとの既定の処理
EVENT_HELP: dict[str, str] = {
    "task.created": "起票エージェントを起動する",
    "decision.requested": "人に知らせる（channel は聞き方の違いだけ）",
    "decision.resolved": "起票エージェントを起動する",
    "task.ready": "配車を待つ",
    "task.started": "実装エージェントを起動する",
    "task.returned": "起票エージェントを起動する（ルールで埋まらなければ人に聞く）",
    "task.submitted": "レビューエージェントを起動する",
    "review.changes_requested": "実装エージェントを起動する（前の PR と指摘を渡す）",
    "review.escalated": "異議のある判断を未決事項として起票に差し戻し、人に知らせる",
    "review.approved": "マージのゲート（`flow.merge = \"human\"`）のときだけ。人のマージを待つと知らせる（`algr watch` にも出る）",
    "task.done": "ルールに従った判断を hit として記録し、人が答えた未決事項があれば蒸留エージェントを起動する",
    "task.failed": "人に上げる",
    "task.reopened": "起票エージェントを起動する",
}

# hook の差し込み口。イベント名のほかに、すべてのイベントを受ける "*" を使える
HOOK_POINTS = (*EVENTS, "*")


class TransitionError(Exception):
    pass


def _no_open_questions(task: Task) -> str | None:
    """未決事項がゼロであること。"""
    if task.open_questions():
        ids = " ".join(q.id for q in task.open_questions())
        return f"{task.id} still has open questions: {ids}"
    return None


def _has_open_questions(task: Task) -> str | None:
    """人に上げる未決事項が残っていること。"""
    if not task.open_questions():
        return f"{task.id} has no open question to ask"
    return None


def _has_pr(task: Task) -> str | None:
    """PR があること。"""
    if not task.pr:
        return f"{task.id} was submitted without a PR"
    return None


def _fills_have_grounds(task: Task) -> str | None:
    """ルールで埋めた未決事項には、従ったルールの ID が付いていること（判断には根拠を付ける）。"""
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
    "review.approved": (_has_pr,),
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
