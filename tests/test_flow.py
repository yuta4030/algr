import pytest

from algr import flow
from algr.model import Question, State, Task


def task(state, **kw):
    return Task(id="I1", title="t", state=state, **kw)


def test_ten_states_and_one_event_per_transition():
    assert len(State) == 10
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


def test_answers_return_to_the_role_that_asked():
    for waiting, (role_state, answered) in flow.WAITING.items():
        assert flow.TRANSITIONS[flow.ASK[role_state]] == (frozenset({role_state}), waiting)
        assert flow.TRANSITIONS[answered] == (frozenset({waiting}), role_state)
    assert set(flow.WAITING) == {State.WAITING_INTAKE, State.WAITING_IMPLEMENT, State.WAITING_REVIEW}


def test_answering_needs_every_open_question_answered():
    t = task(State.WAITING_REVIEW, questions=[Question("I1-1", "q")])
    with pytest.raises(flow.TransitionError, match="open questions: I1-1"):
        flow.check("review.answered", t)


def test_only_a_merged_task_is_done():
    assert flow.TRANSITIONS["task.done"] == (frozenset({State.MERGING}), State.DONE)
    assert flow.TRANSITIONS["review.approved"] == (frozenset({State.REVIEW}), State.MERGING)


def test_every_event_has_a_payload_shape():
    assert set(flow.EVENT_DATA) == set(flow.EVENTS)


def test_payload_must_match_its_shape():
    flow.check_data("implement.asked", {"from": "in-progress", "channel": "inline", "questions": ["I1-1"]})
    with pytest.raises(flow.TransitionError, match="expected"):
        flow.check_data("implement.asked", {"from": "in-progress", "channel": "inline", "question": "I1-1"})
