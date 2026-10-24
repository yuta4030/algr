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
    assert task["state"] == "needs-decision"
    assert [(q["topic"], q["text"]) for q in task["questions"]] == [("auth", "How do users sign in?"), ("", "plain question")]

    code, task, _ = algr("answer", "I1", "I1-1=OAuth", "I1-2=yes")
    assert task["state"] == "ready"

    code, tasks, _ = algr("list", "--state", "ready")
    assert [t["id"] for t in tasks] == ["I1"]


def test_text_output(repo, capsys):
    main(["-C", str(repo), "new", "Add login", "-q", "auth: How?"])
    assert capsys.readouterr().out == "I1 [needs-decision] Add login\n  ? I1-1 How?\n"


def test_errors(algr):
    algr("new", "t")
    assert algr("answer", "I1", "I1-1=x")[2] == {"error": "I1 is ready, not waiting for a decision"}
    assert algr("show", "I9")[2] == {"error": "no such task: I9"}


def test_config_shows_sources(algr, repo):
    _, data, _ = algr("config")
    assert data["root"] == str(repo.resolve())
    assert data["config"]["flow"]["max_parallel"] == 2
