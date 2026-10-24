import pytest

from algr import flow
from algr.model import Question, State, Task


def task(state, **kw):
    return Task(id="I1", title="t", state=state, **kw)


def test_seven_states_and_one_event_per_transition():
    assert len(State) == 7
    assert {target for _, target in flow.TRANSITIONS.values()} == set(State)


def test_ready_requires_no_open_questions():
    t = task(State.INTAKE, questions=[Question("I1-1", "q")])
    with pytest.raises(flow.TransitionError, match="open questions: I1-1"):
        flow.check("task.ready", t)
    t.questions[0].answer, t.questions[0].by = "a", "human"
    assert flow.check("task.ready", t) == State.READY


def test_rule_decisions_must_cite_a_rule():
    t = task(State.INTAKE, questions=[Question("I1-1", "q", answer="a", by="rule")])
    with pytest.raises(flow.TransitionError, match="without citing"):
        flow.check("task.ready", t)


def test_wrong_source_state():
    with pytest.raises(flow.TransitionError, match="not allowed from ready"):
        flow.check("task.done", task(State.READY))


def test_submit_requires_pr():
    with pytest.raises(flow.TransitionError, match="without a PR"):
        flow.check("task.submitted", task(State.IN_PROGRESS))


def test_unknown_event():
    with pytest.raises(flow.TransitionError, match="unknown event"):
        flow.check("task.deployed", task(State.DONE))


def test_human_answers_always_return_to_intake():
    assert flow.TRANSITIONS["decision.resolved"] == (frozenset({State.NEEDS_DECISION}), State.INTAKE)
