import json
import threading
import urllib.error
import urllib.request

import pytest

from algr import ui
from algr.config import Config
from algr.engine import Engine
from conftest import run_until


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
    run_until(gated, lambda: {str(t.state) for t in gated.tasks.list()} >= {"needs-decision", "failed"} and gated.tasks.get("I2").approved)

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
