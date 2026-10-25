"""フェーズ0の完了条件：algr 本体だけで、フェイクアダプタでループが一周する。

エージェントはすべて別プロセスのジョブとして走るので、tick を回して結果を待つ。
"""

import json

from conftest import run_until, state

from algr.model import Event, State, Task


def names(engine, task_id):
    return [e.name for e in engine.events if e.task_id == task_id]


def test_loop_goes_around_once(engine):
    # 起票：起票エージェントがジョブとして走り、ルールが無いので人に聞く
    task = engine.create("Add login", purpose="sign in", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    assert task.state == State.INTAKE  # 起票エージェントはまだ走っている
    assert list(task.jobs) == ["intake"]
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert [q.id for q in task.open_questions()] == ["I1-1"]
    assert engine.notifier.sent() == [{"task_id": "I1", "message": "I1 needs a decision: I1-1"}]  # 人に聞くときは必ず知らせる

    # 人が答えると起票に戻り、ready になる。蒸留エージェントが判断をルールとして提案する
    engine.resolve("I1", {"I1-1": "OAuth"})
    run_until(engine, lambda: engine.decisions.proposals() != [])
    assert engine.decisions.proposals() == [{"id": "P001", "rule": "auth: OAuth", "source": "I1-1"}]

    # 実行：配車され、実装、レビューを経てマージまで進む
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    assert task.pr == "pr/I1"
    assert task.jobs == {}
    assert names(engine, "I1") == [
        "task.created",
        "decision.requested",
        "decision.resolved",
        "task.ready",
        "task.started",
        "task.submitted",
        "task.done",
    ]
    assert engine.tasks.comments("I1") == ["merged: pr/I1"]

    # 蒸留：提案が受け入れられると、同じ論点は次から人に上がらない（Touchless）
    assert engine.decisions.accept("P001") == "R001"
    engine.create("Add signup", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    run_until(engine, lambda: state(engine, "I2") == State.DONE)
    q = engine.tasks.get("I2").questions[0]
    assert (q.answer, q.by, q.rule_ids) == ("OAuth", "rule", ["R001"])
    assert "decision.requested" not in names(engine, "I2")

    usage = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    assert usage == [
        ("escalated", "I1-1"),
        ("decided", "I1-1"),
        ("applied", "I2-1"),
        ("decided", "I2-1"),
    ]
    assert engine.decisions.usage()[-1]["outcome"] == "hit"


def test_questions_and_decisions_are_kept_apart(engine):
    engine.decisions.add("auth: OAuth")
    engine.create("cite rules", questions=[{"text": "Session store?", "topic": "session"}])
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    engine.resolve("I1", {"I1-1": "redis"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    # 未決事項は起票で人やルールが埋めたもの、判断は実装エージェントが PR に載せたもの
    assert [(q.id, q.answer, q.by) for q in task.questions] == [("I1-1", "redis", "human")]
    assert [(d.id, d.answer, d.rule_ids, task.decided_by(d)) for d in task.decisions] == [("I1-2", "OAuth", ["R001"], "rule")]


def test_decision_following_a_human_answer_is_grounded_by_it(engine):
    engine.create("follow answers", questions=[{"text": "Session store?", "topic": "session"}])
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    engine.resolve("I1", {"I1-1": "redis"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    # 人の答えに従った判断は根拠なしにならず、レビューも通る。判断ストアには重ねて記録しない
    d = task.decision("I1-2")
    assert (d.rule_ids, d.question_ids, task.decided_by(d)) == ([], ["I1-1"], "human")
    assert "review.escalated" not in names(engine, "I1")
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
        "task.done",
    ]


def test_returned_during_implementation(engine):
    engine.create("Store sessions needs-db")
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert task.open_questions()[0].text == "Which database?"
    assert task.open_questions()[0].recommended == "sqlite"
    # 差し戻しは起票に戻り、ルールで埋まらないので人に上がる
    assert names(engine, "I1")[-3:] == ["task.started", "task.returned", "decision.requested"]
    assert engine.notifier.sent()[-1]["message"] == "I1 needs a decision: I1-1"

    engine.resolve("I1", {"I1-1": "sqlite"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)


def test_review_escalates_decisions_without_rules(engine):
    engine.create("Refactor guess")
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert "review.escalated" in names(engine, "I1")
    # 根拠の無い判断 I1-1 は、推奨案付きの未決事項 I1-2 として起票に差し戻される
    assert [(d.id, task.decided_by(d)) for d in task.decisions] == [("I1-1", "agent")]
    q = task.question("I1-2")
    assert (q.text, q.open, q.recommended) == ("Module name", True, "util")

    engine.resolve("I1", {"I1-2": "helpers"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    assert task.decisions == []  # やり直しの実装では、前の回の判断は捨てられる
    assert task.question("I1-2").by == "human"


def test_review_objection_to_a_rule_records_reject(engine):
    engine.create("cite rules")  # R001 を引用するが、R001 は存在しない
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    events = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    # ルールに従った判断 I1-1 は却下として記録し、人には新しい未決事項 I1-2 として聞く
    assert events == [("applied", "I1-1"), ("rejected", "I1-1"), ("escalated", "I1-2")]


def test_failed_run_goes_to_human(engine):
    engine.create("Will fail")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: exit 1"


def test_malformed_result_fails_the_task(engine):
    engine.create("badresult")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"].startswith("I1 failed: bad result from implement")


def test_queue_channel_notifies(engine):
    engine.create("From CI", source="auto", questions=[{"text": "Retry or fix?", "topic": "ci"}])
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    assert engine.notifier.sent() == [{"task_id": "I1", "message": "I1 needs a decision: I1-1"}]


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
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    engine.trigger.push(Event("decision.resolved", "I1", {"answers": {"I1-1": "yes"}}))
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
    run_until(engine, lambda: state(engine, task.id) == State.NEEDS_DECISION)
    engine.resolve(task.id, {f"{task.id}-1": "this"})
    run_until(engine, lambda: state(engine, task.id) == State.DONE)
    by_name = {e.name: e.data for e in engine.events}
    assert by_name["task.created"] == {"from": "", "source": "human"}
    assert by_name["decision.resolved"] == {"from": "needs-decision", "questions": [f"{task.id}-1"]}
    assert by_name["task.done"]["from"] == "review"


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


def test_returned_question_is_filled_by_rule_before_asking(engine):
    engine.decisions.add("db: postgres")
    engine.create("Store sessions needs-db")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert "decision.requested" not in names(engine, "I1")
    q = engine.tasks.get("I1").question("I1-1")
    assert (q.answer, q.by) == ("postgres", "rule")


def test_review_requests_changes_and_implement_fixes(engine):
    engine.create("fixme")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert names(engine, "I1")[-4:] == ["task.submitted", "review.changes_requested", "task.submitted", "task.done"]
    task = engine.tasks.get("I1")
    assert (task.pr, task.reworks, task.feedback) == ("pr/I1-fixed", 1, [])


def test_rework_has_a_limit(engine):
    engine.max_rework = 1
    engine.create("nitpick")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert names(engine, "I1").count("review.changes_requested") == 1
    assert engine.notifier.sent()[-1]["message"].startswith("I1 failed: review asked for changes more than 1 times")


def test_human_reopens_a_failed_task(engine, repo):
    engine.create("flaky")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    (repo / "flaky-ok").write_text("", encoding="utf-8")
    engine.reopen("I1", note="fixed the runner")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    reopened = [e for e in engine.events if e.name == "task.reopened"]
    assert reopened[0].data == {"from": "failed", "note": "fixed the runner"}


def test_human_settled_questions_are_not_reopened_by_review(engine):
    engine.create("contrarian", questions=[{"text": "Which?", "topic": "x"}])
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    engine.resolve("I1", {"I1-1": "this"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    assert "review.escalated" not in names(engine, "I1")


def test_merge_gate_waits_for_human(engine):
    engine.merge_gate = True
    engine.create("gated")
    run_until(engine, lambda: state(engine, "I1") == State.APPROVED)
    engine.tick()
    task = engine.tasks.get("I1")
    assert (task.state, task.jobs) == (State.APPROVED, {})  # 承認済みのあいだはレビューを起動し直さない
    assert engine.notifier.sent()[-1]["message"] == "I1 is approved and waiting for merge: pr/I1"
    approved = [e for e in engine.events if e.name == "review.approved"]
    assert [e.data for e in approved] == [{"from": "review", "pr": "pr/I1"}]  # 承認の瞬間に 1 回だけ
    engine.merge("I1")
    assert state(engine, "I1") == State.DONE
    assert engine.events[-1].data == {"from": "approved", "pr": "pr/I1", "merged_by": "human"}


def test_old_task_file_with_approved_flag_reads_as_approved_state():
    old = {"id": "I1", "title": "t", "state": "review", "approved": True}
    task = Task.from_dict(old)
    assert task.state == State.APPROVED
    assert "approved" not in task.to_dict()
    assert Task.from_dict({**old, "approved": False}).state == State.REVIEW


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


def test_token_usage_and_agent_settings(engine, repo):
    engine.agents = {"implement": {"model": "big", "max_tokens": 5000}}
    engine.create("metered")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    # 起票・実装・レビューの 3 つのジョブの分を足し上げる
    assert engine.tasks.get("I1").usage == {"input_tokens": 300, "output_tokens": 30}
    job = json.loads((engine.executor.dir / "I1.implement.2" / "job.json").read_text(encoding="utf-8"))
    assert job["input"]["agent"] == {"model": "big", "max_tokens": 5000}

    from algr import stats

    assert stats.summarize(stats.load(repo / ".algr" / "state" / "events.jsonl"))["tokens_per_task"] == 330.0


def test_watch_keeps_dispatching_until_stopped(engine):
    engine.create("one")
    seen = []
    engine.watch(interval=0.02, on_events=seen.extend, until=lambda: state(engine, "I1") == State.DONE)
    assert [e.name for e in seen][-1] == "task.done"
