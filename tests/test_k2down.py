import json
import sys
from pathlib import Path

import pytest
from conftest import run_until, state, write_config

from algr.adapters import Context
from algr.adapters.k2down import K2down, K2downError
from algr.config import Config
from algr.engine import Engine
from algr.model import State

FAKE = [sys.executable, str(Path(__file__).with_name("fake_k2down.py"))]


def calls(store):
    return [json.loads(line) for line in (store.dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def store(tmp_path):
    return K2down(Context(tmp_path, tmp_path / "home"), command=FAKE)


def test_records_go_to_the_k2down_cli(store):
    assert store.rules() == []  # rules.json がまだ無ければ k2down を呼ばない
    store.apply("I1-1", "How do\nusers sign in?", ["R001"])
    store.apply("I1-2", "no rule", [])  # 根拠の無い判断は apply しない
    store.escalate("I1-3", "Which db?", "no_match")
    store.decide("I1-3", "none", "sqlite")
    store.decide("I1-1", "hit", "OAuth", predicted="OAuth")
    assert calls(store) == [
        ["apply", "--decision", "I1-1", "--problem", "How do users sign in?", "R001"],
        ["escalate", "--decision", "I1-3", "--problem", "Which db?", "--reason", "no_match"],
        ["decide", "--decision", "I1-3", "--outcome", "none", "--actual", "sqlite"],
        ["decide", "--decision", "I1-1", "--outcome", "hit", "--actual", "OAuth", "--predicted", "OAuth"],
    ]


def test_proposals_become_rules_when_accepted(store):
    assert store.proposals() == []  # proposals.json がまだ無ければ k2down を呼ばない
    assert store.propose("db: sqlite", "I1-3") == "P-001"
    assert store.propose("db: sqlite", "I2-1") == "P-001"  # 冪等
    assert store.propose("auth: OAuth", "I1-1") == "P-002"
    store.reject_proposal("P-002")
    assert store.accept("P-001") == "R001"
    assert store.proposals() == []
    assert [(r.id, r.text) for r in store.rules()] == [("R001", "db: sqlite")]  # 内部ノードは除く
    assert calls(store)[0] == ["propose", "add", "db: sqlite", "--decision", "I1-3", "--json"]
    with pytest.raises(K2downError, match=r"^accept: unknown proposal: P-002$"):  # --json のエラーは文言だけにする
        store.accept("P-002")


def test_retire_proposal_retires_the_rule_when_accepted(store):
    store.propose("db: sqlite", "I1-3")
    store.accept("P-001")
    assert store.propose_retire("R001", "db: postgres\nとぶつかる", "I2-1") == "P-002"
    assert store.propose_retire("R001", "again", "I3-1") == "P-002"  # 冪等
    assert store.proposals() == [{"id": "P-002", "kind": "retire", "rule_id": "R001", "decision": "I2-1", "reason": "db: postgres とぶつかる"}]
    assert store.rules() != []  # accept までは外れない
    assert calls(store)[2] == ["propose", "retire", "R001", "--decision", "I2-1", "--reason", "db: postgres とぶつかる", "--json"]
    assert store.accept("P-002") == "R001"
    assert calls(store)[-1] == ["accept", "P-002", "--json"]
    assert store.rules() == [] and store.proposals() == []


def test_report_keeps_only_what_distill_needs(store):
    assert store.report() == {}  # rules.json がまだ無ければ k2down を呼ばない
    store.propose("db: sqlite", "I1-3")
    store.accept("P-001")
    assert store.report() == {
        "rules": [{"id": "R001", "applied": 0, "hit": 0, "miss": 0, "rejected": 0, "decisions": 12}],
        "drop_candidates": ["R001"], "rewrite_candidates": [], "conflict_candidates": [], "declined_proposals": [],
    }  # fmt: skip
    # report から出す外す提案は、元になった決断 ID を持たない
    store.propose_retire("R001", "一度も当たらない", "")
    assert calls(store)[-1] == ["propose", "retire", "R001", "--reason", "一度も当たらない", "--json"]
    assert store.proposals()[0]["decision"] == ""


def test_old_k2down_is_refused_once_at_the_first_call(store, monkeypatch):
    monkeypatch.setenv("FAKE_K2DOWN_VERSION", "k2down 0.3.0")
    with pytest.raises(K2downError, match=r"k2down 0.4.0 or later is required \(found k2down 0.3.0\)"):
        store.escalate("I1-1", "p", "no_match")
    monkeypatch.setenv("FAKE_K2DOWN_VERSION", "k2down 0.10.0")
    store.escalate("I1-1", "p", "no_match")
    store.escalate("I1-2", "p", "no_match")
    assert [c[0] for c in calls(store)] == ["escalate", "escalate"]  # 版は 1 度だけ確かめる


def test_cli_errors_surface(store):
    with pytest.raises(K2downError, match="unknown rule: R404"):
        store.apply("I1-1", "p", ["R404"])
    missing = K2down(Context(store.dir, store.dir), command=["no-such-k2down"])
    with pytest.raises(K2downError, match="not installed"):
        missing.escalate("I1-1", "p", "no_match")


def test_loop_with_k2down(repo, home):
    write_config(repo, f'[adapters.decisions]\nuse = "k2down"\ncommand = {json.dumps(FAKE)}\n')
    engine = Engine.from_config(Config.load(repo))
    engine.create("Add login", questions=[{"text": "How?", "topic": "auth"}])
    run_until(engine, lambda: state(engine, "I1") == State.NEEDS_DECISION)
    engine.resolve("I1", {"I1-1": "OAuth"})
    run_until(engine, lambda: engine.decisions.proposals() != [])
    assert engine.decisions.accept("P-001") == "R001"
    engine.create("Add signup", questions=[{"text": "How?", "topic": "auth"}])
    run_until(engine, lambda: state(engine, "I2") == State.DONE)
    assert engine.tasks.get("I2").question("I2-1").rule_ids == ["R001"]
