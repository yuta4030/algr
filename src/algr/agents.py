"""エージェントとコアの約束：役ごとの入力と結果の形。

エージェント（ホスト側の AI）は実行アダプタがジョブとして起動し、数分から数十分かけて結果を返す。
コアは判断の中身を見ない。結果の形を確かめ、それに従って状態を動かして記録するだけ。

役ごとの結果（JSON）：
- intake：   {"fills": [{"question_id", "answer", "rule_ids"}], "questions": [{"text", "topic"?, "options"?, "recommended"?}]}
             fills はルールで埋まった未決事項。questions は意図から新たに洗い出した未決事項
- implement：{"pr": str, "decisions": [{"text", "answer", "topic"?, "rule_ids"?}]}
             想定外の判断が出たら、勝手に決めずに {"returned": {"text", "topic"?, "recommended"?}}
- review：   {"approved": bool, "objections": {"<決断 ID>": "<理由>"}, "changes"?: ["<修正依頼>"]}
             objections は判断への異議（人に回す）。changes はテスト失敗やバグなど判断以外の指摘（実装に戻す）
- distill：  {"rules": [{"rule", "source"}]}
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from algr.adapters import Rule
from algr.model import Role, Task


class ResultError(Exception):
    """エージェントの結果が約束の形になっていない。"""


@dataclass
class Fill:
    question_id: str
    answer: str
    rule_ids: list[str]


@dataclass
class IntakeResult:
    fills: list[Fill] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImplementResult:
    pr: str | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    returned: dict[str, Any] | None = None


@dataclass
class ReviewResult:
    approved: bool
    objections: dict[str, str] = field(default_factory=dict)  # 決断 ID → 理由
    changes: list[str] = field(default_factory=list)  # 判断以外の修正依頼


@dataclass
class DistillResult:
    rules: list[dict[str, str]] = field(default_factory=list)


def job_input(role: Role, task: Task, rules: list[Rule]) -> dict[str, Any]:
    """ジョブに添える入力。エージェントは自分で判断ストアを引いてもよいが、ここに今のルールを渡しておく。"""
    data: dict[str, Any] = {"rules": [{"id": r.id, "text": r.text} for r in rules]}
    if role == Role.IMPLEMENT:
        # やり直しは一から作らず、前の PR と指摘から差分だけ直す
        data["previous"] = {"pr": task.pr, "decisions": [asdict(d) for d in task.decisions]}
        data["feedback"] = list(task.feedback)
    if role == Role.REVIEW:
        # 人が答えた未決事項は人のゲートを通っているので、異議の対象にしない
        data["settled"] = [q.id for q in task.questions if q.by == "human"]
    if role == Role.DISTILL:
        data["questions"] = [asdict(q) for q in task.questions if q.id in task.to_distill]
    return data


def _need(data: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    if key not in data or not isinstance(data[key], kind):
        raise ResultError(f"missing or malformed {key!r}")
    return data[key]


def _texts(items: Any, *keys: str) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not all(
        isinstance(i, dict) and all(isinstance(i.get(k), str) for k in keys) for i in items
    ):
        raise ResultError(f"expected a list of objects with {', '.join(keys)}")
    return items


def parse(role: Role, data: Any) -> IntakeResult | ImplementResult | ReviewResult | DistillResult:
    if not isinstance(data, dict):
        raise ResultError("result must be an object")
    if role == Role.INTAKE:
        fills = [
            Fill(f["question_id"], f["answer"], list(f.get("rule_ids", [])))
            for f in _texts(data.get("fills", []), "question_id", "answer")
        ]
        return IntakeResult(fills, _texts(data.get("questions", []), "text"))
    if role == Role.IMPLEMENT:
        if "returned" in data:
            returned = _need(data, "returned", dict)
            _texts([returned], "text")
            return ImplementResult(returned=returned)
        return ImplementResult(pr=_need(data, "pr", str), decisions=_texts(data.get("decisions", []), "text", "answer"))
    if role == Role.REVIEW:
        objections = data.get("objections", {})
        if not isinstance(objections, dict):
            raise ResultError("objections must be an object")
        changes = data.get("changes", [])
        if not isinstance(changes, list) or not all(isinstance(c, str) for c in changes):
            raise ResultError("changes must be a list of strings")
        return ReviewResult(bool(_need(data, "approved", bool)), {str(k): str(v) for k, v in objections.items()}, changes)
    if role == Role.DISTILL:
        return DistillResult(_texts(data.get("rules", []), "rule", "source"))
    raise ResultError(f"unknown role: {role}")
