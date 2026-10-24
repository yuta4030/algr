import json
import sys
from pathlib import Path

import pytest
from conftest import run_until, state

from algr import agent
from algr.config import Config
from algr.engine import Engine
from algr.model import State

FAKE_CLAUDE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]


def test_extract_takes_the_last_json_block():
    text = 'まず\n```json\n{"a": 1}\n```\n最後に\n```json\n{"approved": true}\n```\n'
    assert agent.extract(text) == {"approved": True}
    assert agent.extract('{"rules": []}') == {"rules": []}
    with pytest.raises(agent.AgentError):
        agent.extract("no json here")


def test_command_maps_agent_settings():
    argv = agent.command({"model": "small", "max_budget_usd": 0.5, "permission_mode": "acceptEdits", "args": ["--verbose"]})
    assert argv == [
        "claude", "-p", "--output-format", "json", "--no-session-persistence",
        "--model", "small", "--max-budget-usd", "0.5", "--permission-mode", "acceptEdits", "--verbose",
    ]  # fmt: skip


@pytest.fixture
def claude_engine(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude"))
    (repo / ".algr" / "config.toml").write_text(
        f"""
[adapters.executor]
use = "fake.process"
command = {json.dumps([sys.executable, "-m", "algr.agent"])}
max_parallel = 4

[agents.default]
command = {json.dumps(FAKE_CLAUDE)}
[agents.review]
model = "small"
""",
        encoding="utf-8",
    )
    (repo / ".algr" / "prompts").mkdir()
    (repo / ".algr" / "prompts" / "review.md").write_text("社内：公開 API の差分は必ず異議にする\n", encoding="utf-8")
    return Engine.from_config(Config.load(repo))


def test_loop_with_claude(claude_engine, tmp_path):
    engine = claude_engine
    engine.create("Add login")
    run_until(engine, lambda: state(engine, "I1") == State.DONE)
    task = engine.tasks.get("I1")
    assert task.usage == {"input_tokens": 300, "output_tokens": 15}  # キャッシュの読み込みも入力に数える

    log = tmp_path / "claude"
    prompt = (log / "I1.review.3.prompt.md").read_text(encoding="utf-8")
    # 変わりにくいものから順に並ぶ：共通 → 役 → 利用側の追記 → ジョブ
    order = ["# algr のエージェント", "# 役：レビュー", "社内：公開 API", "# ジョブ"]
    assert [prompt.index(s) for s in order] == sorted(prompt.index(s) for s in order)
    assert '"agent"' not in prompt  # 実行の設定はエージェントに見せない
    argv = json.loads((log / "I1.review.3.argv.json").read_text(encoding="utf-8"))
    assert argv[argv.index("--model") + 1] == "small"
    assert "--model" not in json.loads((log / "I1.implement.2.argv.json").read_text(encoding="utf-8"))


def test_agent_error_fails_the_task(claude_engine):
    engine = claude_engine
    engine.create("agent-error")
    run_until(engine, lambda: state(engine, "I1") == State.FAILED)
    assert engine.notifier.sent()[-1]["message"] == "I1 failed: exit 1"
