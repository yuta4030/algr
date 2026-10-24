"""アダプタの IF。コアは外部（タスク管理、実行場所、判断の保存、通知、トリガー）をこれ越しにしか触らない。

アダプタは冪等に作る。イベントを再送しても壊れないようにするためだ。
各チームは .algr/ に数十行のアダプタを書き、.algr/config.toml の `use` で指す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algr.model import Event, State, Task


@dataclass(frozen=True)
class Context:
    """アダプタに渡す実行環境。パスはリポジトリのルートからの相対で解決する。"""

    root: Path  # リポジトリのルート（.algr/ を含むディレクトリ）
    home: Path  # 個人の ~/.algr/

    def path(self, value: str | Path) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.root / p


@runtime_checkable
class TaskAdapter(Protocol):
    """issue を取得し、状態の読み書きとコメントを行う。"""

    def create(self, task: Task) -> Task:
        """id を振って保存し、振った後のタスクを返す。task.id は空で渡される。"""
        ...

    def get(self, task_id: str) -> Task: ...

    def list(self, state: State | None = None) -> list[Task]: ...

    def save(self, task: Task) -> None: ...

    def comment(self, task_id: str, text: str) -> None: ...


@runtime_checkable
class TriggerAdapter(Protocol):
    """外部のイベントをフローのイベントに変換する。"""

    def poll(self) -> list[Event]:
        """前回以降に届いたイベントを返す。返したイベントは二度と返さない。"""
        ...


class RunState(StrEnum):
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunStatus:
    state: RunState
    # 完了時の結果。{"pr": str, "decisions": [...]} か {"returned": {"text": ..., "topic": ...}}
    result: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@runtime_checkable
class ExecutorAdapter(Protocol):
    """実装エージェントを起動する。attach(task) と stop(task) は任意。"""

    def start(self, task: Task) -> None: ...

    def status(self, task: Task) -> RunStatus: ...

    def capacity(self) -> int:
        """いま追加で起動できる数。同時実行数の上限はコアの設定で持つ。"""
        ...


@dataclass(frozen=True)
class Rule:
    id: str
    text: str


@runtime_checkable
class DecisionStore(Protocol):
    """ルールの提示、判断の記録と集計。操作は k2down の CLI に合わせてある。"""

    def rules(self) -> list[Rule]: ...

    def apply(self, decision: str, problem: str, rule_ids: list[str]) -> None: ...

    def escalate(self, decision: str, problem: str, reason: str, rule_ids: list[str] | None = None) -> None: ...

    def decide(self, decision: str, outcome: str, actual: str, predicted: str | None = None) -> None: ...

    def propose(self, rule: str, source: str) -> str:
        """1 行ルールの追加を提案し、提案の ID を返す。マージ（人の確認）をもってルールになる。"""
        ...


@runtime_checkable
class Notifier(Protocol):
    """人の判断待ちを届ける。"""

    def notify(self, task: Task, message: str) -> None: ...


KINDS: dict[str, type] = {
    "task": TaskAdapter,
    "trigger": TriggerAdapter,
    "executor": ExecutorAdapter,
    "decisions": DecisionStore,
    "notify": Notifier,
}
