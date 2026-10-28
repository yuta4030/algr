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
    assert [e["name"] for e in data["events"]] == ["intake.asked"]
    _, tasks, _ = algr("list", "--state", "waiting")
    assert [(t["id"], t["state"]) for t in tasks] == [("I1", "waiting-intake")]

    # 答えは一部ずつでもよい。そろうまでは判断待ちのまま
    _, task, _ = algr("answer", "I1", "I1-1=OAuth")
    assert task["state"] == "waiting-intake"
    _, task, _ = algr("answer", "I1", "I1-2=yes")
    assert task["state"] == "intake"
    algr("run", "--interval", "0.02", "--timeout", "10")

    _, tasks, _ = algr("list", "--state", "done")
    assert [t["id"] for t in tasks] == ["I1"]


def test_text_output(repo, capsys):
    main(["-C", str(repo), "new", "Add login", "-q", "auth: How?"])
    assert capsys.readouterr().out == "I1 [intake] Add login\n  ? I1-1 How?\n"
    main(["-C", str(repo), "run", "--interval", "0.02"])
    assert capsys.readouterr().out == "I1 intake.asked\n"


def test_unencodable_output(repo, monkeypatch):
    # cp932 のコンソールで出せない文字（U+2013）を含んでも落ちず、? に置き換えて出す
    import io
    import sys

    out = io.TextIOWrapper(io.BytesIO(), encoding="cp932", newline="\n")
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["-C", str(repo), "new", "日付 – 範囲"]) == 0
    out.flush()
    assert out.buffer.getvalue().decode("cp932") == "I1 [intake] 日付 ? 範囲\n"


def test_errors(algr):
    algr("new", "t")
    assert algr("answer", "I1", "I1-1=x")[2] == {"error": "I1 is intake, not waiting for a decision"}
    assert algr("show", "I9")[2] == {"error": "no such task: I9"}


def test_config_shows_sources(algr, repo):
    _, data, _ = algr("config")
    assert data["root"] == str(repo.resolve())
    assert data["config"]["flow"]["max_parallel"] == 2


def test_reopen_needs_a_failed_task(algr):
    algr("new", "t")
    assert algr("reopen", "I1")[2] == {"error": "I1 is intake, not failed"}


def test_cancel(algr):
    algr("new", "t")
    _, task, _ = algr("cancel", "I1", "--note", "changed my mind")
    assert task["state"] == "canceled"
    assert algr("cancel", "I1")[2] == {"error": "I1 is canceled and cannot be canceled"}


def test_merge_is_not_a_command(repo, capsys):
    with pytest.raises(SystemExit):
        main(["-C", str(repo), "merge", "I1"])
    assert "invalid choice: 'merge'" in capsys.readouterr().err


def test_depends(algr):
    algr("new", "a")
    algr("new", "b")
    _, task, _ = algr("depends", "I2", "--add", "I1")
    assert task["depends"] == ["I1"]
    assert algr("depends", "I1", "--add", "I2")[2] == {"error": "depends would form a cycle: I1 -> I2 -> I1"}
    assert algr("depends", "I2", "--add", "I9")[2] == {"error": "no such task: I9"}
    _, task, _ = algr("depends", "I2", "--remove", "I1")
    assert task["depends"] == []


def test_distill_a_policy_and_retire_the_rule_it_replaces(algr, repo, capsys):
    code, task, _ = algr("distill", "sqlite", "--topic", "db")
    assert code == 0
    assert (task["source"], task["state"], list(task["jobs"])) == ("policy", "done", ["distill"])
    algr("run", "--interval", "0.02", "--timeout", "10")
    _, data, _ = algr("proposals")
    assert data["proposals"] == [{"id": "P001", "kind": "add", "rule": "db: sqlite", "decision": "I1-1", "scopes": []}]
    algr("accept", "P001")

    # 同じ方針はもうルールにあるので、提案は出ない
    algr("distill", "sqlite", "--topic", "db")
    algr("run", "--interval", "0.02", "--timeout", "10")
    assert algr("proposals")[1]["proposals"] == []

    # ぶつかる方針は、新しいルールと古いルールを外す提案になる。外れるのは accept したときだけ
    algr("distill", "postgres", "--topic", "db")
    algr("run", "--interval", "0.02", "--timeout", "10")
    main(["-C", str(repo), "proposals"])
    assert capsys.readouterr().out == "P001 db: postgres (from I3-1)\nP002 retire R001: db: postgres とぶつかる (from I3-1)\n"
    assert [r["id"] for r in algr("rules")[1]["rules"]] == ["R001"]
    algr("accept", "P002")
    algr("accept", "P001")
    assert algr("rules")[1]["rules"] == [{"id": "R002", "text": "db: postgres"}]

    # 方針のタスクはフローに流れず、イベントも出さない
    _, data, _ = algr("stats")
    assert data["stats"]["tasks"] == 0
    assert [t["state"] for t in algr("list")[1]] == ["done", "done", "done"]


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
    # 聞いたのは起票だけ。どの判断待ちで聞いたかを状態ごとに数える
    assert s["asked_by_state"] == {"waiting-intake": 1, "waiting-implement": 0, "waiting-review": 0}
    assert s["merge_changes_requested"] == 0
    assert s["jobs_by_role"]["implement"] == 2 and s["jobs_per_task"] >= 3


def test_watch_stops_on_sigterm(repo):
    import signal
    import subprocess
    import sys
    import time

    argv = [sys.executable, "-m", "algr", "-C", str(repo), "watch", "--interval", "0.05"]
    if sys.platform == "win32":
        # Windows の SIGTERM はプロセスを即座に終わらせるので、止める合図は CTRL_BREAK（SIGBREAK）で送る
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        time.sleep(1.0)
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True)
        time.sleep(1.0)
        proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
