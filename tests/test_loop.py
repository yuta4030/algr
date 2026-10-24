"""フェーズ0の完了条件：algr 本体だけで、フェイクアダプタでループが一周する。"""

from conftest import run_until

from algr.model import Event, State


def names(engine, task_id):
    return [e.name for e in engine.events if e.task_id == task_id]


def test_loop_goes_around_once(engine):
    # 起票：ルールが無いので、人に聞く
    task = engine.create("Add login", purpose="sign in", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    assert task.state == State.NEEDS_DECISION
    assert [q.id for q in task.open_questions()] == ["I1-1"]
    assert engine.notifier.sent() == []  # 同期の起票はその場で聞くので通知しない

    # 人が答えると起票に戻り、ready になる。判断はルールとして提案される
    task = engine.resolve("I1", {"I1-1": "OAuth"})
    assert task.state == State.READY
    assert engine.decisions.proposals() == [{"id": "P001", "rule": "auth: OAuth", "source": "I1-1"}]

    # 実行：配車され、PR が出て、レビューを経てマージまで進む
    run_until(engine, lambda: engine.tasks.get("I1").state == State.DONE)
    assert engine.tasks.get("I1").pr == "pr/I1"
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
    second = engine.create("Add signup", questions=[{"text": "How do users sign in?", "topic": "auth"}])
    assert second.state == State.READY
    q = second.questions[0]
    assert (q.answer, q.by, q.rule_ids) == ("OAuth", "rule", ["R001"])
    run_until(engine, lambda: engine.tasks.get("I2").state == State.DONE)
    assert "decision.requested" not in names(engine, "I2")

    usage = [(u["event"], u["decision"]) for u in engine.decisions.usage() if "decision" in u]
    assert usage == [
        ("escalated", "I1-1"),
        ("decided", "I1-1"),
        ("applied", "I2-1"),
        ("decided", "I2-1"),
    ]
    assert engine.decisions.usage()[-1]["outcome"] == "hit"


def test_events_are_logged_to_state_dir(engine, repo):
    engine.create("No questions")
    lines = (repo / ".algr" / "state" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [line.split('"name": "')[1].split('"')[0] for line in lines] == ["task.created", "task.ready"]


def test_returned_during_implementation(engine):
    engine.create("Store sessions needs-db")
    run_until(engine, lambda: engine.tasks.get("I1").state == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert task.open_questions()[0].text == "Which database?"
    assert task.open_questions()[0].recommended == "sqlite"
    assert "task.returned" in names(engine, "I1")
    assert engine.notifier.sent()[-1]["message"].startswith("I1 was returned")

    engine.resolve("I1", {"I1-1": "sqlite"})
    run_until(engine, lambda: engine.tasks.get("I1").state == State.DONE)


def test_review_escalates_decisions_without_rules(engine):
    engine.create("Refactor guess")
    run_until(engine, lambda: engine.tasks.get("I1").state == State.NEEDS_DECISION)
    task = engine.tasks.get("I1")
    assert "review.escalated" in names(engine, "I1")
    q = task.question("I1-1")
    assert (q.open, q.recommended) == (True, "util")

    engine.resolve("I1", {"I1-1": "helpers"})
    run_until(engine, lambda: engine.tasks.get("I1").state == State.DONE)
    assert engine.tasks.get("I1").question("I1-1").by == "human"


def test_failed_run_goes_to_human(engine):
    engine.create("Will fail")
    run_until(engine, lambda: engine.tasks.get("I1").state == State.FAILED)
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: exit 1"


def test_queue_channel_notifies(engine):
    task = engine.create("From CI", source="auto", questions=[{"text": "Retry or fix?", "topic": "ci"}])
    assert task.state == State.NEEDS_DECISION
    assert engine.notifier.sent() == [{"task_id": "I1", "message": "I1 needs a decision: I1-1"}]


def test_dispatch_respects_priority_and_parallelism(engine):
    engine.max_parallel = 1
    engine.create("low", priority=0)
    engine.create("high", priority=5)
    started = engine.dispatch()
    assert [t.title for t in started] == ["high"]
    assert engine.tasks.get("I1").state == State.READY


def test_trigger_events_enter_the_flow(engine):
    engine.trigger.push(Event("task.created", "", {"title": "From inbox", "questions": [{"text": "Q", "topic": "x"}]}))
    engine.tick()
    assert engine.tasks.get("I1").state == State.NEEDS_DECISION
    engine.trigger.push(Event("decision.resolved", "I1", {"answers": {"I1-1": "yes"}}))
    run_until(engine, lambda: engine.tasks.get("I1").state == State.DONE)


def test_hooks_run_after_default_handler(repo, home):
    from conftest import write_config

    from algr.config import Config
    from algr.engine import Engine

    (repo / ".algr" / "hooks.py").write_text(
        "seen = []\ndef on_ready(event, engine):\n    seen.append((event.task_id, str(engine.tasks.get(event.task_id).state)))\n",
        encoding="utf-8",
    )
    write_config(repo, '[hooks]\n"task.ready" = [".algr/hooks.py:on_ready"]\n')
    engine = Engine.from_config(Config.load(repo))
    engine.create("hooked")
    hook = engine.hooks["task.ready"][0]
    assert hook.__globals__["seen"] == [("I1", "ready")]
