"""アダプタの IF。コアは外部（タスク管理、実行場所、判断の保存、マージ、通知、トリガー）をこれ越しにしか触らない。

アダプタは冪等に作る。イベントを再送しても壊れないようにするためだ。
各チームは .algr/ に数十行のアダプタを書き、.algr/config.toml の `use` で指す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algr.model import Event, Job, State, Task


@dataclass(frozen=True)
class Context:
    """アダプタに渡す実行環境。パスはリポジトリのルートからの相対で解決する。"""

    root: Path  # リポジトリのルート（.algr/ を含むディレクトリ）
    home: Path  # 個人の ~/.algr/

    def path(self, value: str | Path) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.root / p


class AdapterError(Exception):
    """アダプタが外部の操作に失敗した。CLI は終了コード 1 で知らせる。"""


class Conflict(Exception):
    """保存しようとしたタスクが、読んだあとにほかの処理で書き換えられていた。読み直してやり直す。"""


@runtime_checkable
class TaskAdapter(Protocol):
    """issue を取得し、状態の読み書きとコメントを行う。"""

    def create(self, task: Task) -> Task:
        """id を振って保存し、振った後のタスクを返す。task.id は空で渡される。"""
        ...

    def get(self, task_id: str) -> Task: ...

    def list(self, state: State | None = None) -> list[Task]: ...

    def save(self, task: Task) -> None:
        """楽観ロックで保存する。保存済みの版が task.version と違えば Conflict を投げ、同じなら版を 1 つ進めて保存する。

        task.version は保存後の版に書き換える。続けて同じ task を保存してよい。
        """
        ...

    def comment(self, task_id: str, text: str) -> None: ...


@runtime_checkable
class TriggerAdapter(Protocol):
    """外部のイベントをフローのイベントに変換する。"""

    def poll(self) -> list[Event]:
        """前回以降に届いたイベントを返す。返したイベントは二度と返さない。"""
        ...


class RunState(StrEnum):
    RUNNING = "running"
    BLOCKED = "blocked"  # エージェントが立場を待っている。attach を持つアダプタなら入って答えられる
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunStatus:
    state: RunState
    # 完了時の結果。形は役ごとに決まっている（algr.agents を参照）
    result: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    # 使ったトークン数（例：{"input_tokens": 1200, "output_tokens": 300}）。分かる実行アダプタだけが返す
    usage: dict[str, int] = field(default_factory=dict)
    # エージェントとの往復の回数。トークン数とは種類が違うので usage に混ぜない。分かる実行アダプタだけが返す
    turns: int = 0


@runtime_checkable
class ExecutorAdapter(Protocol):
    """エージェントを起動する。起票・実装・レビュー・蒸留のどれもジョブとしてここから起動する。

    エージェントは数分から数十分かかるので、start はすぐ返し、結果は status で受け取る。
    attach(job_id) と stop(job_id) は任意。
    """

    def start(self, job: Job) -> None:
        """job を起動する。同じ job.id で二度呼ばれても二重に起動しない。"""
        ...

    def status(self, job_id: str) -> RunStatus:
        """知らない job_id には FAILED を返す。"""
        ...

    def capacity(self) -> int:
        """いま追加で起動できる数。同時実行数の上限はコアの設定で持つ。"""
        ...


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    scopes: tuple[str, ...] = ()  # ルールの範囲（役の名前）。空なら範囲なし（どの範囲にも当たる）


# 判断ストアの report() が返すキー。蒸留に渡すのはこれだけ（日ごとの数などは渡さない）
REPORT_KEYS = ("rules", "drop_candidates", "rewrite_candidates", "conflict_candidates", "declined_proposals")


@runtime_checkable
class DecisionStore(Protocol):
    """ルールの提示、判断の記録と集計。操作は k2down の CLI に合わせてある。

    任意で report() -> dict を持てる。持っていれば、蒸留エージェントにルールの手入れの候補として渡す。
    形は REPORT_KEYS のキーを持つ dict（k2down report --json の一部）：ルールごとの数（rules: [{"id", "applied", "hit", "miss", "rejected", "decisions"}]）、
    落とす候補（drop_candidates）、書き直す候補（rewrite_candidates）、ぶつかる組（conflict_candidates）、却下された提案（declined_proposals）。
    """

    def rules(self, scope: str | None = None) -> list[Rule]:
        """ルールを返す。scope（役の名前など）を渡すと、その範囲のルールと範囲の無いルールだけを返す。None なら全ルール。"""
        ...

    def apply(self, decision: str, problem: str, rule_ids: list[str]) -> None: ...

    def reject(self, decision: str, problem: str, rule_ids: list[str]) -> None:
        """当てたルールが、この問題には当てはまらなかったと記録する。"""
        ...

    def escalate(self, decision: str, problem: str, reason: str, rule_ids: list[str] | None = None) -> None: ...

    def decide(self, decision: str, outcome: str, actual: str, predicted: str | None = None) -> None: ...

    def propose(self, rule: str, source: str, scopes: list[str] | None = None) -> str:
        """1 行ルールの追加を提案し、提案の ID を返す。source は元になった決断 ID で、無ければ空。scopes はルールの範囲で、空なら範囲なし。メタプログラマーの accept をもってルールになる。同じ提案は二重に出さない。"""
        ...

    def propose_retire(self, rule_id: str, reason: str, source: str) -> str:
        """ルールを外す提案をし、提案の ID を返す。外すのはメタプログラマーの accept を通したときだけ。同じルールの提案は二重に出さない。"""
        ...

    def proposals(self) -> list[dict[str, str]]:
        """まだ受け入れも却下もされていない提案。

        足す提案は {"id", "kind": "add", "rule", "decision", "scopes"}、外す提案は {"id", "kind": "retire", "rule_id", "reason", "decision"}。
        decision は propose / propose_retire の source に渡した決断 ID。report の候補から出した提案では空。scopes は propose に渡した範囲で、範囲なしなら空。
        """
        ...

    def accept(self, proposal_id: str) -> str:
        """メタプログラマーが提案を受け入れ、足す提案ならルールにし、外す提案ならルールを外す。足した（外した）ルールの ID を返す。"""
        ...

    def reject_proposal(self, proposal_id: str) -> None:
        """メタプログラマーが提案を却下する。"""
        ...


class MergeState(StrEnum):
    MERGED = "merged"  # マージ先に入った
    OPEN = "open"  # まだ入っていない。待つ
    CHANGES = "changes"  # PJ（PR をレビュー・マージするチーム）から修正依頼が来た
    CLOSED = "closed"  # マージされずに閉じられた


@dataclass
class MergeStatus:
    state: MergeState
    changes: list[str] = field(default_factory=list)  # CHANGES のときの PJ の指摘。実装の feedback に渡す
    message: str = ""


@runtime_checkable
class MergeAdapter(Protocol):
    """承認されたタスクの PR をマージ先に入れる。マージ先（main か統合ブランチか）はアダプタの設定で決まり、コアは同じ。

    タスク管理（TaskAdapter）とは別の種類にする。コードの置き場（git、PR）はタスク管理と別のシステムになりうる。
    同じシステムなら、1 つのクラスで両方の IF を実装して設定の 2 か所から指せばよい。
    """

    def request(self, task: Task) -> None:
        """task.pr のマージを頼む。同じタスクの同じ PR で二度呼ばれても二重に頼まない。

        コアは merging にいる間、tick ごとに呼ぶ。修正依頼のあとに出し直した PR は新しい頼みになる。
        """
        ...

    def status(self, task: Task) -> MergeStatus:
        """マージ先に入ったか。CHANGES は、いまの PR を頼んだあとに来た指摘だけを返す（直した PR に古い指摘を返さない）。"""
        ...


@runtime_checkable
class Notifier(Protocol):
    """立場（クライアントとメタプログラマー）への知らせを届ける。判断待ちと失敗は必ず知らせる。"""

    def notify(self, task: Task, message: str) -> None: ...


KINDS: dict[str, type] = {
    "task": TaskAdapter,
    "trigger": TriggerAdapter,
    "executor": ExecutorAdapter,
    "decisions": DecisionStore,
    "merge": MergeAdapter,
    "notify": Notifier,
}
