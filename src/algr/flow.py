"""状態遷移とイベント、遷移ごとの不変条件。

タスクは 10 の状態を移り、遷移ごとにイベントが 1 つ出る。そのイベントがそのまま hook の差し込み口になる。
立場（クライアント）に渡るのは、起票・実装・レビューそれぞれの判断待ち（waiting-*）と failed だけ。
答えが出ると、聞いた役の状態に戻ってその役が走り直す。
"""

from __future__ import annotations

from collections.abc import Callable

from algr.model import State, Task

S = State

# イベント名 → (遷移元の集合, 遷移先)。task.created は新規タスクなので遷移元を持たない。
TRANSITIONS: dict[str, tuple[frozenset[State], State]] = {
    "task.created": (frozenset(), S.INTAKE),
    "intake.asked": (frozenset({S.INTAKE}), S.WAITING_INTAKE),
    "intake.answered": (frozenset({S.WAITING_INTAKE}), S.INTAKE),
    "task.ready": (frozenset({S.INTAKE}), S.READY),
    "task.started": (frozenset({S.READY}), S.IN_PROGRESS),
    "implement.asked": (frozenset({S.IN_PROGRESS}), S.WAITING_IMPLEMENT),
    "implement.answered": (frozenset({S.WAITING_IMPLEMENT}), S.IN_PROGRESS),
    "task.submitted": (frozenset({S.IN_PROGRESS}), S.REVIEW),
    "review.changes_requested": (frozenset({S.REVIEW}), S.IN_PROGRESS),
    "review.asked": (frozenset({S.REVIEW}), S.WAITING_REVIEW),
    "review.answered": (frozenset({S.WAITING_REVIEW}), S.REVIEW),
    "review.approved": (frozenset({S.REVIEW}), S.MERGING),
    "merge.changes_requested": (frozenset({S.MERGING}), S.IN_PROGRESS),
    "task.done": (frozenset({S.MERGING}), S.DONE),
    "task.failed": (frozenset({S.INTAKE, S.IN_PROGRESS, S.REVIEW, S.MERGING}), S.FAILED),
    "task.reopened": (frozenset({S.FAILED}), S.INTAKE),
}

EVENTS = tuple(TRANSITIONS)

# 判断待ちの状態 → (聞いた役の状態, 答えが出たときのイベント)。戻り先はタスクの項目ではなく状態で持つ
WAITING: dict[State, tuple[State, str]] = {
    S.WAITING_INTAKE: (S.INTAKE, "intake.answered"),
    S.WAITING_IMPLEMENT: (S.IN_PROGRESS, "implement.answered"),
    S.WAITING_REVIEW: (S.REVIEW, "review.answered"),
}

# 役の状態 → 立場に聞くときのイベント
ASK = {S.INTAKE: "intake.asked", S.IN_PROGRESS: "implement.asked", S.REVIEW: "review.asked"}

# イベントごとのペイロード（Event.data）のキー。hook はこれを当てにしてよい。
# どのイベントにも遷移元の状態 "from" が付く（task.created は ""）。決断 ID の並びは必ず "questions" で渡す。
EVENT_DATA: dict[str, tuple[str, ...]] = {
    "task.created": ("source",),
    "intake.asked": ("channel", "questions"),
    "intake.answered": ("questions",),
    "task.ready": (),
    "task.started": (),
    "implement.asked": ("channel", "questions"),
    "implement.answered": ("questions",),
    "task.submitted": ("pr",),
    "review.changes_requested": ("changes",),
    "review.asked": ("channel", "questions", "reasons"),
    "review.answered": ("questions",),
    "review.approved": ("pr",),
    "merge.changes_requested": ("changes",),
    "task.done": ("pr",),
    "task.failed": ("role", "message"),
    "task.reopened": ("note",),
}

# 状態とイベントの説明。README の表と algr ui のヘルプはここから作るか、ここと突き合わせる（tests/test_help.py）
STATE_HELP: dict[State, str] = {
    S.INTAKE: "起票。未決事項をルールで埋め、埋まらないものはクライアントに聞く",
    S.WAITING_INTAKE: "起票の問いへのクライアントの答え待ち。答えがそろうと起票に戻る",
    S.READY: "未決事項が無くなり、配車を待つ。依存先がすべて done になるまで配車しない",
    S.IN_PROGRESS: "実装エージェントが実装し、PR を出す",
    S.WAITING_IMPLEMENT: "実装の問いへのクライアントの答え待ち。答えがそろうと実装に戻る",
    S.REVIEW: "レビューエージェントが PR と判断を確かめる",
    S.WAITING_REVIEW: "レビューの問いへのクライアントの答え待ち。答えがそろうとレビューに戻り、レビューが承認か修正依頼かを決める",
    S.MERGING: "マージのアダプタに頼み、マージ先に入るのを待つ。PJ の修正依頼が来たら実装に戻る",
    S.DONE: "マージ先に入った。クライアントが答えた未決事項があれば蒸留エージェントが走る",
    S.FAILED: "失敗。理由はタスクの failure に残り、reopen すると起票に戻る",
}

# イベントごとの既定の処理
EVENT_HELP: dict[str, str] = {
    "task.created": "起票エージェントを起動する",
    "intake.asked": "クライアントに知らせる（channel は聞き方の違いだけ）",
    "intake.answered": "起票エージェントを起動し直す",
    "task.ready": "配車を待つ",
    "task.started": "実装エージェントを起動する",
    "implement.asked": "クライアントに知らせる",
    "implement.answered": "実装エージェントを起動し直す（前の PR と答えを渡す）",
    "task.submitted": "レビューエージェントを起動する",
    "review.changes_requested": "実装エージェントを起動する（前の PR と指摘を渡す）",
    "review.asked": "異議のある判断と新しい問いを未決事項にし、クライアントに知らせる",
    "review.answered": "レビューエージェントを起動し直す（答えを持って承認か修正依頼かを決める）",
    "review.approved": "マージのアダプタにマージを頼む（`request`）",
    "merge.changes_requested": "PJ の指摘を修正依頼として、実装エージェントを起動する",
    "task.done": "ルールに従った判断を hit として記録し、クライアントが答えた未決事項があれば蒸留エージェントを起動する",
    "task.failed": "クライアントに知らせる",
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
    """クライアントに聞く未決事項が残っていること。"""
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
    "intake.asked": (_has_open_questions,),
    "intake.answered": (_no_open_questions,),
    "task.ready": (_no_open_questions, _fills_have_grounds),
    "implement.asked": (_has_open_questions,),
    "implement.answered": (_no_open_questions,),
    "task.submitted": (_has_pr,),
    "review.changes_requested": (_has_pr,),
    "review.asked": (_has_open_questions,),
    "review.answered": (_no_open_questions,),
    "review.approved": (_has_pr, _no_open_questions),
    "merge.changes_requested": (_has_pr,),
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
