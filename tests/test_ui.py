import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import run_until

from algr import ui
from algr.config import Config
from algr.engine import Engine


@pytest.fixture
def gated(repo) -> Engine:
    (repo / ".algr" / "config.toml").write_text(
        (repo / ".algr" / "config.toml").read_text(encoding="utf-8").replace("[flow]\n", '[flow]\nmerge = "human"\n'),
        encoding="utf-8",
    )
    return Engine.from_config(Config.load(repo))


def by_id(snap):
    return {t["id"]: t for t in snap["tasks"]}


def test_snapshot_shows_who_has_each_task(gated):
    gated.create("ask", questions=[{"text": "How?"}])
    gated.create("merge me")
    gated.create("fail")
    gated.create("later", depends=["I2"])
    run_until(gated, lambda: {str(t.state) for t in gated.tasks.list()} >= {"needs-decision", "failed", "approved"})

    tasks = by_id(ui.snapshot(gated))
    assert tasks["I1"]["progress"] | {"since": None, "created": None} == {
        "stage": "intake", "waiting": "human", "roles": [], "blocked_by": [], "failure": None,
        "created": None, "since": None, "returned": 0, "changes": 0, "asked": 1,
    }  # fmt: skip
    assert tasks["I1"]["actions"] == [{"name": "answer", "cli": 'algr answer I1 I1-1="..."'}]
    assert tasks["I2"]["progress"]["stage"] == "review"
    assert tasks["I2"]["actions"] == [{"name": "merge", "cli": "algr merge I2"}]
    assert tasks["I3"]["progress"]["stage"] == "in-progress"
    assert tasks["I3"]["progress"]["failure"] == {"role": "implement", "message": "exit 1"}
    assert tasks["I3"]["actions"][0]["name"] == "reopen"
    assert tasks["I4"]["progress"]["waiting"] == "deps"
    assert tasks["I4"]["progress"]["blocked_by"] == ["I2"]
    assert [e["name"] for e in tasks["I2"]["events"]][:2] == ["task.created", "task.ready"]


def test_server_serves_page_and_snapshot_on_localhost_only(engine):
    engine.create("hello")
    httpd = ui.server(engine, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as res:
            assert b"<title>algr</title>" in res.read()
        with urllib.request.urlopen(base + "/api/snapshot") as res:
            assert [t["id"] for t in json.load(res)["tasks"]] == ["I1"]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/", headers={"Host": "evil.example"}))
        assert e.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(base + "/api/snapshot", data=b"{}"))
        assert e.value.code == 501  # 書き込みはまだ受けない
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_history_counts_transitions_rules_and_jobs(gated):
    gated.decisions.add("auth: OAuth")
    gated.create("login", questions=[{"topic": "auth", "text": "How?"}])
    gated.create("ask", questions=[{"topic": "db", "text": "Where?"}])
    run_until(gated, lambda: str(gated.tasks.get("I1").state) == "approved" and str(gated.tasks.get("I2").state) == "needs-decision")
    gated.resolve("I2", {"I2-1": "sqlite"})
    run_until(gated, lambda: str(gated.tasks.get("I2").state) == "approved" and not gated.tasks.get("I2").jobs)
    assert gated.decisions.proposals() == []  # 蒸留は done になってから
    gated.merge("I2")
    run_until(gated, lambda: not gated.tasks.get("I2").jobs)

    h = ui.history(gated)
    edges = {(e["event"], e["from"]): e["count"] for e in h["flow"]["edges"]}
    assert edges[("task.started", "ready")] == 2
    assert edges[("decision.requested", "intake")] == 1
    assert edges[("task.reopened", "failed")] == 0  # 通っていない遷移も図に出す
    states = {s["state"]: s for s in h["flow"]["states"]}
    assert (states["approved"]["now"], states["done"]["now"]) == (1, 1)
    assert states["needs-decision"]["stay_median_s"] is not None

    rules = h["rules"]
    assert [(r["id"], [u["id"] for u in r["uses"]]) for r in rules["rules"]] == [("R001", ["I1-1"])]
    assert [(c["topic"], c["human"]) for c in rules["candidates"]] == [("db", 1)]
    assert rules["proposals"] == [{"id": "P001", "kind": "add", "rule": "db: sqlite", "decision": "I2-1", "question": {"text": "Where?", "answer": "sqlite"}}]

    assert {(j["task_id"], j["role"]) for j in h["jobs"]} >= {("I1", "intake"), ("I1", "implement"), ("I1", "review"), ("I2", "distill")}
    assert all(j["end"] and j["state"] == "completed" for j in h["jobs"])
    assert [b["tasks"] for b in h["trend"]["buckets"]] == [2]
    assert h["trend"]["unit"] == "day"


def test_trend_buckets_by_week_after_three_weeks():
    def created(task_id, ts):
        return {"name": "task.created", "task_id": task_id, "data": {"from": "", "source": "human"}, "ts": ts}

    t = ui.trend([created("I1", "2026-09-01T10:00:00+00:00"), created("I2", "2026-09-30T10:00:00+00:00")])
    assert t["unit"] == "week"
    assert [(b["start"], b["tasks"]) for b in t["buckets"]] == [
        ("2026-08-31", 1), ("2026-09-07", 0), ("2026-09-14", 0), ("2026-09-21", 0), ("2026-09-28", 1),
    ]  # fmt: skip
