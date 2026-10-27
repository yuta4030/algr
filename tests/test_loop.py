"""フェーズ0の完了条件：algr 本体だけで、フェイクアダプタでループが一周する。

エージェントはすべて別プロセスのジョブとして走るので、tick を回して結果を待つ。
"""

import json
import time

import pytest
from conftest import run_until, state

from algr.adapters import AdapterError, MergeState, MergeStatus
from algr.agents import ResultError, parse
from algr.flow import TransitionError
from algr.model import Decision, Event, Role, State


def names(engine, task_id):
    return [e.name for e in engine.events if e.task_id == task_id]


def started_roles(engine):
    lines = engine.event_log.read_text(encoding="utf-8").splitlines()
    return [r["role"] for r in map(json.loads, lines) if "job_started" in r]


def test_loop_goes_around_once(engine):
    # 起票：起票エージェントがジョブとして走り、ルールが無いのでクライアントに聞く
    task = engine.create("Add login", purpose="sign in", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    assert task.state == State.INTAKE  # 起票エージェントはまだ走っている
    assert list(task.jobs) == ["intake"]
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    task = engine.tasks.get("I1")
    assert [q.id for q in task.open_questions()] == ["I1-1"]
    assert engine.notifier.sent() == [{"task_id": "I1", "message": "I1 needs a decision from intake: I1-1"}]  # 聞くときは必ず知らせる

    # 答えると起票に戻り、ready になる。マージされると、蒸留エージェントが答えをルールとして提案する
    engine.resolve("I1", {"I1-1": "OAuth"})
    run_until(engine, lambda: engine.decisions.proposals() != [])
    assert engine.decisions.proposals() == [{"id": "P001", "kind": "add", "rule": "auth: OAuth", "decision": "I1-1"}]

    # 実行：配車され、実装、レビューを経て、マージのアダプタに頼んでマージ先に入る
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    assert task.pr == "pr/I1"
    assert task.jobs == {}
    assert names(engine, "I1") == [
        "task.created",
        "intake.asked",
        "intake.answered",
        "task.ready",
        "task.started",
        "task.submitted",
        "review.approved",
        "task.done",
    ]
    assert engine.merger.requests() == {"I1": "pr/I1"}
    assert engine.tasks.comments("I1") == ["merged: pr/I1"]

    # 蒸留：提案が受け入れられると、同じ論点は次からクライアントに聞かない（Touchless）
    assert engine.decisions.accept("P001") == "R001"
    engine.create("Add signup", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    run_until(engine, lambda: state(engine, "I2") == State.DONE)
    q = engine.tasks.get("I2").questions[0]
    assert (q.answer, q.by, q.rule_ids) == ("OAuth", "rule", ["R001"])
    assert "intake.asked" not in names(engine, "I2")

    usage = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    assert usage == [
        ("escalated", "I1-1"),
        ("decided", "I1-1"),
        ("proposed", "I1-1"),
        ("applied", "I2-1"),
        ("decided", "I2-1"),
    ]
    assert engine.decisions.usage()[-1]["outcome"] == "hit"


def test_questions_and_decisions_are_kept_apart(engine):
    engine.decisions.add("auth: OAuth")
    engine.create("cite rules", questions=[{"text": "Session store?", "topic": "session"}])
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    engine.resolve("I1", {"I1-1": "redis"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    # 未決事項はクライアントやルールが埋めたもの、判断は実装エージェントが PR に載せたもの
    assert [(q.id, q.answer, q.by) for q in task.questions] == [("I1-1", "redis", "human")]
    assert [(d.id, d.answer, d.rule_ids, task.decided_by(d)) for d in task.decisions] == [("I1-2", "OAuth", ["R001"], "rule")]


def test_decision_following_a_human_answer_is_grounded_by_it(engine):
    engine.create("follow answers", questions=[{"text": "Session store?", "topic": "session"}])
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    engine.resolve("I1", {"I1-1": "redis"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    # クライアントの答えに従った判断は根拠なしにならず、レビューも通る。判断ストアには重ねて記録しない
    d = task.decision("I1-2")
    assert (d.rule_ids, d.question_ids, task.decided_by(d)) == ([], ["I1-1"], "human")
    assert "review.asked" not in names(engine, "I1")
    assert {u["decision"] for u in engine.decisions.usage() if "decision" in u} == {"I1-1"}


def test_decision_following_a_rule_fill_inherits_rule(engine):
    engine.decisions.add("db: postgres")
    engine.create("follow fills", questions=[{"text": "Which database?", "topic": "db"}])
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    d = task.decision("I1-2")
    # ルール ID は未決事項の側にだけ残り、hit も未決事項の側でだけ記録される
    assert (d.rule_ids, task.decided_by(d)) == ([], "rule")
    usage = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    assert usage == [("applied", "I1-1"), ("decided", "I1-1")]


def test_decision_following_an_unknown_question_fails(engine):
    engine.create("follow-bad")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.events[-1].data["message"] == "decisions follow unknown questions: I99-1"


def test_events_are_logged_to_state_dir(engine, repo):
    engine.create("No questions")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    records = [json.loads(line) for line in (repo / ".algr" / "state" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["role"] for r in records if "job" in r] == ["intake", "implement", "review"]  # ジョブの記録も残る
    assert [r["name"] for r in records if "name" in r] == [
        "task.created",
        "task.ready",
        "task.started",
        "task.submitted",
        "review.approved",
        "task.done",
    ]


def test_implement_asks_and_runs_again_with_the_answer(engine):
    engine.create("Store sessions needs-db")
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_IMPLEMENT)
    task = engine.tasks.get("I1")
    assert task.open_questions()[0].text == "Which database?"
    assert task.open_questions()[0].recommended == "sqlite"
    assert names(engine, "I1")[-2:] == ["task.started", "implement.asked"]
    assert engine.notifier.sent()[-1]["message"] == "I1 needs a decision from implement: I1-1"

    # 答えると起票には戻らず、実装だけが答えを持って走り直す
    engine.resolve("I1", {"I1-1": "sqlite"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert names(engine, "I1")[3:] == ["implement.asked", "implement.answered", "task.submitted", "review.approved", "task.done"]
    assert started_roles(engine) == ["intake", "implement", "implement", "review", "distill"]


def test_review_asks_about_decisions_without_rules(engine):
    engine.create("Refactor guess")
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_REVIEW)
    task = engine.tasks.get("I1")
    assert names(engine, "I1")[-1] == "review.asked"
    assert engine.events[-1].data == {"from": "review", "channel": "inline", "questions": ["I1-2"], "reasons": {"I1-1": "decided without a rule"}}
    # 根拠の無い判断 I1-1 は、推奨案付きの未決事項 I1-2 になり、判断はその答えに従うものになる
    q = task.question("I1-2")
    assert (q.text, q.open, q.recommended) == ("Module name", True, "util")
    assert task.decision("I1-1").question_ids == ["I1-2"]

    # 答えると起票にも実装にも戻らず、レビューが答えを持って走り直して承認する
    engine.resolve("I1", {"I1-2": "helpers"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    assert names(engine, "I1")[-4:] == ["review.asked", "review.answered", "review.approved", "task.done"]
    assert started_roles(engine) == ["intake", "implement", "review", "review", "distill"]
    assert (task.question("I1-2").by, task.decided_by(task.decision("I1-1"))) == ("human", "human")


def test_review_can_ask_before_merging(engine):
    # ルール「マージ前に必ずクライアントに聞く」に当たったレビューは、判断に結び付かない問いを聞く
    engine.create("ask-merge")
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_REVIEW)
    task = engine.tasks.get("I1")
    assert [(q.id, q.text, q.asked) for q in task.open_questions()] == [("I1-1", "Merge it?", True)]
    assert engine.events[-1].data["reasons"] == {}
    assert engine.merger.requests() == {}
    engine.resolve("I1", {"I1-1": "yes"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)


def test_review_objection_to_a_rule_records_reject(engine):
    engine.create("cite rules")  # R001 を引用するが、R001 は存在しない
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_REVIEW)
    events = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    # ルールに従った判断 I1-1 は却下として記録し、クライアントには新しい未決事項 I1-2 として聞く
    assert events == [("applied", "I1-1"), ("rejected", "I1-1"), ("escalated", "I1-2")]
    # 却下されたルールは判断から外す。マージされても hit にしない
    assert engine.tasks.get("I1").decision("I1-1").rule_ids == []


def test_failed_run_is_notified(engine):
    engine.create("Will fail")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: exit 1"


def test_malformed_result_fails_the_task(engine):
    engine.create("badresult")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"].startswith("I1 failed: bad result from implement")


def test_queue_channel_notifies(engine):
    engine.create("From CI", source="auto", questions=[{"text": "Retry or fix?", "topic": "ci"}])
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    assert engine.notifier.sent() == [{"task_id": "I1", "message": "I1 needs a decision from intake: I1-1"}]
    assert engine.events[-1].data["channel"] == "queue"


def test_dispatch_respects_priority_and_parallelism(engine):
    engine.max_parallel = 0  # 両方 ready になるまで配車させない
    engine.create("low", priority=0)
    engine.create("high", priority=5)
    run_until(engine, lambda: {t.state for t in engine.tasks.list()} == {State.READY})
    engine.max_parallel = 1
    assert [t.title for t in engine.dispatch()] == ["high"]
    assert state(engine, "I1") == State.READY


def test_capacity_defers_jobs_to_a_later_tick(engine, repo):
    engine.executor.max_parallel = 1
    engine.create("slow one")
    run_until(engine, lambda: state(engine, "I1") == State.IN_PROGRESS)
    engine.create("waits")
    assert engine.tasks.get("I2").jobs == {}  # capacity が空くまで起票エージェントは起動しない
    (repo / "stop").write_text("", encoding="utf-8")
    run_until(engine, lambda: state(engine, "I1") == State.DONE and state(engine, "I2") == State.DONE)


def test_lost_job_fails_the_task(engine):
    engine.create("slow lost")
    run_until(engine, lambda: state(engine, "I1") == State.IN_PROGRESS)
    job_id = engine.tasks.get("I1").jobs["implement"]
    engine.executor.stop(job_id)
    (engine.executor.dir / job_id / "exit").unlink()  # 終了コードを残さずに消えた
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: lost"


def test_run_returns_when_idle(engine):
    engine.create("one")
    engine.run(interval=0.02, timeout=10)
    assert state(engine, "I1") == State.DONE


def test_trigger_events_enter_the_flow(engine):
    engine.trigger.push(Event("task.created", "", {"title": "From inbox", "questions": [{"text": "Q", "topic": "x"}]}))
    engine.tick()
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    # 回答はどの役が聞いたかを知らなくてよい。判断待ちの状態から intake.answered が起きる
    engine.trigger.push(Event("task.answered", "I1", {"answers": {"I1-1": "yes"}}))
    run_until(engine, lambda: state(engine, "I1") == State.DONE)


def test_hooks_run_after_default_handler(repo, home):
    from conftest import write_config

    from algr.config import Config
    from algr.engine import Engine

    (repo / ".algr" / "hooks.py").write_text(
        "seen = []\ndef on_created(event, engine):\n    seen.append(sorted(engine.tasks.get(event.task_id).jobs))\n",
        encoding="utf-8",
    )
    write_config(repo, '[hooks]\n"task.created" = [".algr/hooks.py:on_created"]\n')
    engine = Engine.from_config(Config.load(repo))
    engine.create("hooked")
    hook = engine.hooks["task.created"][0]
    assert hook.__globals__["seen"] == [["intake"]]  # 既定の処理（起票エージェントの起動）のあとに呼ばれる


def test_events_carry_their_payload_and_source_state(engine):
    task = engine.create("payload", questions=[{"text": "Which?", "topic": "x"}])
    run_until(engine, lambda: state(engine, task.id) == State.WAITING_INTAKE)
    engine.resolve(task.id, {f"{task.id}-1": "this"})
    run_until(engine, lambda: state(engine, task.id) == State.DONE)
    by_name = {e.name: e.data for e in engine.events}
    assert by_name["task.created"] == {"from": "", "source": "human"}
    assert by_name["intake.answered"] == {"from": "waiting-intake", "questions": [f"{task.id}-1"]}
    assert by_name["review.approved"] == {"from": "review", "pr": f"pr/{task.id}"}
    assert by_name["task.done"] == {"from": "merging", "pr": f"pr/{task.id}"}


def test_failing_hook_does_not_stop_the_flow(repo, home):
    from conftest import write_config

    from algr.config import Config
    from algr.engine import Engine

    (repo / ".algr" / "hooks.py").write_text(
        "seen = []\n"
        "def boom(event, engine):\n    raise RuntimeError('boom')\n"
        "def every(event, engine):\n    seen.append(event.name)\n",
        encoding="utf-8",
    )
    write_config(repo, '[hooks]\n"task.created" = [".algr/hooks.py:boom"]\n"*" = [".algr/hooks.py:every"]\n')
    engine = Engine.from_config(Config.load(repo))
    task = engine.create("hooked")
    run_until(engine, lambda: state(engine, task.id) == State.DONE)
    seen = engine.hooks["*"][0].__globals__["seen"]
    assert seen == [e.name for e in engine.events]  # "*" はすべてのイベントを受ける
    notes = (repo / ".algr" / "state" / "events.jsonl").read_text(encoding="utf-8")
    assert "hook boom failed on task.created: RuntimeError: boom" in notes


def test_stale_save_is_rejected(engine):
    import pytest

    from algr.adapters import Conflict

    engine.create("one")
    stale, fresh = engine.tasks.get("I1"), engine.tasks.get("I1")
    fresh.purpose = "changed elsewhere"
    engine.tasks.save(fresh)
    stale.priority = 9
    with pytest.raises(Conflict):
        engine.tasks.save(stale)
    assert engine.tasks.get("I1").purpose == "changed elsewhere"


def test_review_requests_changes_and_implement_fixes(engine):
    engine.create("fixme")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert names(engine, "I1")[-5:] == ["task.submitted", "review.changes_requested", "task.submitted", "review.approved", "task.done"]
    task = engine.tasks.get("I1")
    assert (task.pr, task.reworks, task.feedback) == ("pr/I1-fixed", 1, [])


def test_rework_has_a_limit(engine):
    engine.max_rework = 1
    engine.create("nitpick")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert names(engine, "I1").count("review.changes_requested") == 1
    assert engine.notifier.sent()[-1]["message"].startswith("I1 failed: review asked for changes more than 1 times")


def test_reopen_a_failed_task(engine, repo):
    engine.create("flaky")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    (repo / "flaky-ok").write_text("", encoding="utf-8")
    engine.reopen("I1", note="fixed the runner")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    reopened = [e for e in engine.events if e.name == "task.reopened"]
    assert reopened[0].data == {"from": "failed", "note": "fixed the runner"}


def test_client_settled_questions_are_not_reopened_by_review(engine):
    engine.create("contrarian", questions=[{"text": "Which?", "topic": "x"}])
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    engine.resolve("I1", {"I1-1": "this"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert "review.asked" not in names(engine, "I1")


class ScriptedMerge:
    """マージのアダプタの代わり。status は statuses を頭から 1 つずつ返し、尽きたら最後を返し続ける。"""

    def __init__(self, *statuses):
        self.statuses, self.requested = list(statuses), []

    def request(self, task):
        if (task.id, task.pr) not in self.requested:
            self.requested.append((task.id, task.pr))

    def status(self, task):
        status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return status


def test_merging_waits_until_the_merge_target_has_it(engine):
    engine.merger = ScriptedMerge(MergeStatus(MergeState.OPEN), MergeStatus(MergeState.OPEN), MergeStatus(MergeState.MERGED))
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.MERGING)
    assert engine.tasks.get("I1").jobs == {}  # merging のあいだはジョブを起動しない
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert engine.merger.requested == [("I1", "pr/I1")]  # tick ごとに頼み直しても、二重には頼まない


def test_project_changes_go_back_to_implement(engine):
    engine.merger = ScriptedMerge(MergeStatus(MergeState.CHANGES, ["rename the flag"]), MergeStatus(MergeState.MERGED))
    engine.max_rework = 0
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    changes = [e for e in engine.events if e.name == "merge.changes_requested"]
    assert [e.data for e in changes] == [{"from": "merging", "changes": ["rename the flag"]}]
    # PJ の指摘は feedback として実装に渡り、直した PR がもう一度レビューを通る。max_rework には数えない
    assert started_roles(engine) == ["intake", "implement", "review", "implement", "review"]
    job = json.loads((engine.executor.dir / "I1.implement.4" / "job.json").read_text(encoding="utf-8"))
    assert (job["input"]["feedback"], engine.tasks.get("I1").reworks) == (["rename the flag"], 0)

    # PJ の修正依頼は algr のレビューの見逃しとして数え、走り直したジョブも役ごとに出る
    from algr import stats

    summary = stats.summarize(stats.load(engine.event_log))
    assert summary["merge_changes_requested"] == 1
    assert summary["jobs_by_role"] == {"intake": 1, "implement": 2, "review": 2}
    assert summary["jobs_per_task"] == 5.0


def test_closed_without_merging_fails(engine):
    engine.merger = ScriptedMerge(MergeStatus(MergeState.CLOSED, message="closed by the project"))
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.tasks.get("I1").failure == {"role": "merge", "message": "closed by the project"}
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: closed by the project"


def test_merge_adapter_errors_are_retried(engine):
    engine.merger = ScriptedMerge(AdapterError("network down"), MergeStatus(MergeState.MERGED))
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert "network down" in engine.event_log.read_text(encoding="utf-8")


def test_cancel_stops_running_jobs_and_drops_their_results(engine, repo):
    engine.create("slow one")
    run_until(engine, lambda: state(engine, "I1") == State.IN_PROGRESS)
    job_id = engine.tasks.get("I1").jobs["implement"]
    engine.cancel("I1", note="split into two")
    (repo / "stop").write_text("", encoding="utf-8")
    run_until(engine, lambda: not engine.tasks.get("I1").jobs)
    assert state(engine, "I1") == State.CANCELED  # 止めたジョブの結果は捨てられ、状態は動かない
    assert (engine.executor.dir / job_id / "exit").read_text(encoding="utf-8") == "143"
    assert engine.events[-1].data == {"from": "in-progress", "note": "split into two"}


def test_cancel_a_waiting_or_failed_task_and_tell_its_dependents(engine):
    engine.create("ask", questions=[{"text": "Which?"}])
    engine.create("after", depends=["I1"])
    run_until(engine, lambda: state(engine, "I1") == State.WAITING_INTAKE)
    engine.cancel("I1")
    assert engine.notifier.sent()[-1]["message"] == "I1 was canceled (blocks I2)"
    engine.create("Will fail")
    run_until(engine, lambda: state(engine, "I3") == State.FAILED)
    assert engine.cancel("I3").state == State.CANCELED


def test_done_cannot_be_canceled(engine):
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    with pytest.raises(TransitionError, match="I1 is done and cannot be canceled"):
        engine.cancel("I1")


def test_dependent_task_waits_until_dependency_is_done(engine, repo):
    engine.create("first slow")
    engine.create("second", depends=["I1"])
    run_until(engine, lambda: state(engine, "I1") == State.IN_PROGRESS and state(engine, "I2") == State.READY)
    engine.tick()
    assert state(engine, "I2") == State.READY  # 同時実行数が空いていても、依存先が done になるまで配車しない
    (repo / "stop").write_text("", encoding="utf-8")
    run_until(engine, lambda: state(engine, "I2") == State.DONE)
    order = [(e.task_id, e.name) for e in engine.events if e.name in ("task.done", "task.started")]
    assert order.index(("I1", "task.done")) < order.index(("I2", "task.started"))


def test_failed_dependency_is_reported_with_its_dependents(engine):
    engine.create("Will fail")
    engine.create("after", depends=["I1"])
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert "I1 failed: exit 1 (blocks I2)" in [m["message"] for m in engine.notifier.sent()]


def test_distill_cannot_retire_unknown_rules(engine, repo):
    task = engine.request_distill("anything")
    # ジョブが終わるのを tick せずに待ち、結果を知らないルールを外す提案に差し替える
    run = engine.executor.dir / task.jobs["distill"]
    deadline = time.monotonic() + 10
    while not (run / "exit").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    retire = [{"rule_id": "R404", "reason": "x", "source": "I1-1"}]
    (run / "result.json").write_text(json.dumps({"rules": [], "retire": retire}), encoding="utf-8")
    engine.tick()
    assert engine.decisions.proposals() == []
    assert engine.notifier.sent()[-1]["message"] == "I1 distill failed: bad result from distill: retire unknown rules: R404"


def test_agents_can_file_follow_up_tasks(engine):
    engine.create("spawn")
    run_until(engine, lambda: state(engine, "I1") == State.DONE and "I2" in [t.id for t in engine.tasks.list()])
    follow = engine.tasks.get("I2")
    assert (follow.title, follow.source, follow.depends) == ("follow-up", "auto", ["I1"])
    assert [(q.text, q.topic) for q in follow.questions] == [("Q?", "f")]


def test_follow_up_with_unknown_dependency_fails_the_job(engine):
    engine.create("spawn-bad")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert "unknown tasks: I99" in engine.notifier.sent()[-1]["message"]


def test_depends_can_be_changed_later(engine, repo):
    engine.create("first slow")
    engine.create("second", depends=["I1"])
    engine.create("third")
    run_until(engine, lambda: state(engine, "I1") == State.IN_PROGRESS and state(engine, "I2") == State.READY)
    assert engine.depend("I2", add=["I3", "I3"]).depends == ["I1", "I3"]
    assert engine.depend("I2", remove=["I1", "I3"]).depends == []
    run_until(engine, lambda: state(engine, "I2") == State.DONE)  # 依存を外したので、I1 を待たずに配車される
    assert state(engine, "I1") == State.IN_PROGRESS
    (repo / "stop").write_text("", encoding="utf-8")


def test_depends_must_exist_and_not_form_a_cycle(engine):
    engine.create("a")
    engine.create("b", depends=["I1"])
    engine.create("c", depends=["I2"])
    with pytest.raises(KeyError):
        engine.depend("I1", add=["I9"])
    with pytest.raises(TransitionError, match="does not depend on I3"):
        engine.depend("I1", remove=["I3"])
    with pytest.raises(TransitionError, match="cycle: I1 -> I3 -> I2 -> I1"):
        engine.depend("I1", add=["I3"])
    with pytest.raises(TransitionError, match="cycle: I1 -> I1"):
        engine.depend("I1", add=["I1"])
    assert engine.tasks.get("I1").depends == []


def test_token_usage_and_agent_settings(engine, repo):
    engine.agents = {"implement": {"model": "big", "max_tokens": 5000}}
    engine.create("metered")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    # 起票・実装・レビューの 3 つのジョブの分を足し上げる
    assert engine.tasks.get("I1").usage == {"input_tokens": 300, "output_tokens": 30}
    job = json.loads((engine.executor.dir / "I1.implement.2" / "job.json").read_text(encoding="utf-8"))
    assert job["input"]["agent"] == {"model": "big", "max_tokens": 5000}

    from algr import stats

    summary = stats.summarize(stats.load(repo / ".algr" / "state" / "events.jsonl"))
    assert summary["tokens_per_task"] == 330.0
    assert summary["tokens_per_task_by_kind"] == {"input_tokens": 300.0, "output_tokens": 30.0}
    assert summary["tokens_by_role"] == {"intake": 110, "implement": 110, "review": 110}


def test_watch_keeps_dispatching_until_stopped(engine):
    engine.create("one")
    seen = []
    engine.watch(interval=0.02, on_events=seen.extend, until=lambda: state(engine, "I1") == State.DONE)
    assert [e.name for e in seen][-1] == "task.done"


def test_distill_reads_the_report_and_proposes_retiring_unused_rules(engine):
    engine.decisions.add("auth: OAuth")
    for i in range(10):  # 判断が 10 件たまっても、このルールは一度も当たらない
        engine.decisions.decide(f"X{i}-1", "none", "x")
    assert engine.decisions.report()["drop_candidates"] == ["R001"]
    task = engine.request_distill("anything")
    run_until(engine, lambda: not engine.tasks.get(task.id).jobs)
    job = json.loads((engine.executor.dir / "I1.distill.1" / "job.json").read_text(encoding="utf-8"))
    assert job["input"]["report"]["drop_candidates"] == ["R001"]
    # report から出した提案は、元になった決断 ID を持たない
    assert engine.decisions.proposals() == [{"id": "P001", "kind": "retire", "rule_id": "R001", "reason": "report: 一度も当たっていない", "decision": ""}]


def test_jobs_get_only_the_rules_in_their_scope(engine):
    engine.decisions.add("shared")
    engine.decisions.add("for implement", scope="implement")
    engine.decisions.add("for review", scope="review")
    engine.create("one")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)

    def given(job_id):
        job = json.loads((engine.executor.dir / job_id / "job.json").read_text(encoding="utf-8"))
        return [r["id"] for r in job["input"]["rules"]]

    assert given("I1.intake.1") == ["R001"]
    assert given("I1.implement.2") == ["R001", "R002"]
    assert given("I1.review.3") == ["R001", "R003"]
    # レビューには、判断が引いたほかの範囲のルールも足す。蒸留には全ルールを渡す
    task = engine.tasks.get("I1")
    task.decisions = [Decision("I1-1", "db", "sqlite", rule_ids=["R002"])]
    assert [r.id for r in engine._rules_for(Role.REVIEW, task)] == ["R001", "R003", "R002"]
    assert [r.id for r in engine._rules_for(Role.DISTILL, task)] == ["R001", "R002", "R003"]


def test_distill_proposes_rules_with_a_scope(engine):
    result = parse(Role.DISTILL, {"rules": [{"rule": "x", "scope": "implement"}, {"rule": "y"}]})
    assert [(r["rule"], r["scope"]) for r in result.rules] == [("x", "implement"), ("y", "")]
    with pytest.raises(ResultError, match="scope"):
        parse(Role.DISTILL, {"rules": [{"rule": "x", "scope": "src/algr"}]})
    proposal_id = engine.decisions.propose("x", "I1-1", scope="implement")
    assert engine.decisions.proposals()[0]["scope"] == "implement"
    rule_id = engine.decisions.accept(proposal_id)
    assert engine.decisions.rules(scope="review") == []
    assert [r.id for r in engine.decisions.rules(scope="implement")] == [rule_id]
