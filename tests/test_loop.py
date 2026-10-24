"""フェーズ0の完了条件：algr 本体だけで、フェイクアダプタでループが一周する。

エージェントはすべて別プロセスのジョブとして走るので、tick を回して結果を待つ。
"""

from conftest import run_until, state

from algr.model import Event, State


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
    assert engine.notifier.sent() == []  # 同期の起票はその場で聞くので通知しない

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
    assert [(d.id, d.answer, d.rule_ids, d.by) for d in task.decisions] == [("I1-2", "OAuth", ["R001"], "rule")]


def test_events_are_logged_to_state_dir(engine, repo):
    engine.create("No questions")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    lines = (repo / ".algr" / "state" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [line.split('"name": "')[1].split('"')[0] for line in lines] == [
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
    assert "task.returned" in names(engine, "I1")
    assert engine.notifier.sent()[-1]["message"].startswith("I1 was returned")

    engine.resolve("I1", {"I1-1": "sqlite"})
    run_until(engine, lambda: state(engine, "I1") == State.DONE)


def test_review_escalates_decisions_without_rules(engine):
    engine.create("Refactor guess")
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert "review.escalated" in names(engine, "I1")
    # 根拠の無い判断 I1-1 は、推奨案付きの未決事項 I1-2 として起票に差し戻される
    assert [(d.id, d.by) for d in task.decisions] == [("I1-1", "agent")]
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
