import json

import pytest

from algr.cli import main


@pytest.fixture
def algr(repo, capsys):
    def run(*args):
        code = main(["-C", str(repo), "--json", *args])
        out, err = capsys.readouterr()
        return code, (json.loads(out) if out else None), (json.loads(err) if err else None)

    return run


def test_init(tmp_path, home, capsys):
    assert main(["-C", str(tmp_path), "init"]) == 0
    assert (tmp_path / ".algr" / "config.toml").exists()
    assert main(["-C", str(tmp_path), "init"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_new_answer_and_list(algr):
    code, task, _ = algr("new", "Add login", "-q", "auth: How do users sign in?", "-q", "plain question")
    assert code == 0
    assert task["state"] == "intake"
    assert [(q["topic"], q["text"]) for q in task["questions"]] == [("auth", "How do users sign in?"), ("", "plain question")]

    _, data, _ = algr("run", "--interval", "0.02", "--timeout", "10")
    assert [e["name"] for e in data["events"]] == ["decision.requested"]

    _, task, _ = algr("answer", "I1", "I1-1=OAuth", "I1-2=yes")
    assert task["state"] == "intake"
    algr("run", "--interval", "0.02", "--timeout", "10")

    _, tasks, _ = algr("list", "--state", "done")
    assert [t["id"] for t in tasks] == ["I1"]


def test_text_output(repo, capsys):
    main(["-C", str(repo), "new", "Add login", "-q", "auth: How?"])
    assert capsys.readouterr().out == "I1 [intake] Add login\n  ? I1-1 How?\n"
    main(["-C", str(repo), "run", "--interval", "0.02"])
    assert capsys.readouterr().out == "I1 decision.requested\n"


def test_errors(algr):
    algr("new", "t")
    assert algr("answer", "I1", "I1-1=x")[2] == {"error": "I1 is intake, not waiting for a decision"}
    assert algr("show", "I9")[2] == {"error": "no such task: I9"}


def test_config_shows_sources(algr, repo):
    _, data, _ = algr("config")
    assert data["root"] == str(repo.resolve())
    assert data["config"]["flow"]["max_parallel"] == 2


def test_reopen_and_merge(algr):
    algr("new", "t")
    assert algr("reopen", "I1")[2] == {"error": "I1 is intake, not failed"}
    assert algr("merge", "I1")[2] == {"error": "I1 is not approved and waiting for merge"}


def test_stats(algr):
    algr("new", "plain")
    algr("new", "asks", "-q", "auth: How?")
    algr("run", "--interval", "0.02", "--timeout", "10")
    algr("answer", "I2", "I2-1=OAuth")
    algr("run", "--interval", "0.02", "--timeout", "10")
    _, data, _ = algr("stats")
    s = data["stats"]
    assert (s["tasks"], s["done"], s["stp_rate"], s["interventions_per_task"]) == (2, 2, 0.5, 0.5)
    assert (s["return_rate_implement"], s["return_rate_review"]) == (0.0, 0.0)
    assert s["decision_wait_median_s"] >= 0


def test_watch_stops_on_sigterm(repo):
    import signal
    import subprocess
    import sys
    import time

    proc = subprocess.Popen([sys.executable, "-m", "algr", "-C", str(repo), "watch", "--interval", "0.05"], stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
