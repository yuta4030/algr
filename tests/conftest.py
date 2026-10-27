from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from algr.config import Config
from algr.engine import Engine

AGENT = Path(__file__).with_name("fake_agent.py")


def write_config(root: Path, extra: str = "") -> None:
    (root / ".algr").mkdir(parents=True, exist_ok=True)
    (root / ".algr" / "config.toml").write_text(
        f"""
[flow]
max_parallel = 2

[adapters.executor]
use = "fake.process"
command = {json.dumps([sys.executable, str(AGENT)])}
max_parallel = 4
{extra}
""",
        encoding="utf-8",
    )


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("ALGR_HOME", str(path))
    return path


@pytest.fixture
def repo(tmp_path, home) -> Path:
    root = tmp_path / "repo"
    write_config(root)
    return root


@pytest.fixture
def engine(repo) -> Engine:
    return Engine.from_config(Config.load(repo))


def run_until(engine: Engine, done: Callable[[], bool], timeout: float = 10.0) -> None:
    """tick を回し、done が真になるまで待つ。エージェントは別プロセスで走る。"""
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError("timed out: " + " ".join(f"{t.id}:{t.state}" for t in engine.tasks.list()))
        engine.tick()
        time.sleep(0.02)


def state(engine: Engine, task_id: str) -> str:
    return str(engine.tasks.get(task_id).state)


class HoldMerge:
    """マージのアダプタの代わり。release するまで open を返し、マージ先で止まったタスクを作る。"""

    def __init__(self) -> None:
        self.released: set[str] = set()

    def request(self, task) -> None:
        pass

    def status(self, task):
        from algr.adapters import MergeState, MergeStatus

        return MergeStatus(MergeState.MERGED if task.id in self.released else MergeState.OPEN)

    def release(self, task_id: str) -> None:
        self.released.add(task_id)
