"""エージェントとコアの約束：役ごとの入力と結果の形。

エージェント（ホスト側の AI）は実行アダプタがジョブとして起動し、数分から数十分かけて結果を返す。
コアは判断の中身を見ない。結果の形を確かめ、それに従って状態を動かして記録するだけ。

役ごとの起動する時と結果（JSON）の形は ROLE_HELP。
- intake：   fills はルールで埋まった未決事項。questions は意図から新たに洗い出した未決事項
- implement：question_ids は判断が従った未決事項。未決事項の答えから外れるなら判断せず、差し戻す。
             想定外の判断が出たら、勝手に決めずに returned で返す
- review：   objections は判断への異議（人に回す）。changes はテスト失敗やバグなど判断以外の指摘（実装に戻す）
- distill：  source は元になった決断 ID（report の候補から出した提案には無い）。rules は足す提案、retire は外す提案。どちらも人が accept してはじめてルールに効く。
             人の答えのほか、input.report（落とす候補・書き直す候補）からも外す提案と言い直し（新しいルールと外す提案の対）を出す

intake と implement の結果には、見つけた別件 "found": [{"title", "purpose"?, "questions"?, "depends"?, "priority"?}] を付けられる。
algr は別件をタスクにせず、1 件ごとに遷移しないイベント task.found で返す。利用者のタスク管理に起票するかは hook が決める。
depends にはタスク管理での ID をそのまま書いてよい（algr は確かめない）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from algr.adapters import Rule
from algr.model import Role, Task

# 役ごとの起動する時（when）と結果の形（result）。README の表と algr ui のヘルプはここと突き合わせる（tests/test_help.py）
ROLE_HELP: dict[Role, dict[str, str]] = {
    Role.INTAKE: {
        "name": "起票",
        "when": "intake にいる間",
        "result": '`{"fills": [{"question_id", "answer", "rule_ids"}], "questions": [{"text", "topic"?, "options"?, "recommended"?}]}`',
    },
    Role.IMPLEMENT: {
        "name": "実装",
        "when": "in-progress にいる間",
        "result": '`{"pr", "decisions": [{"text", "answer", "topic"?, "rule_ids"?, "question_ids"?}]}`、'
        '差し戻すときは `{"returned": {"text", "topic"?, "recommended"?}}`',
    },
    Role.REVIEW: {
        "name": "レビュー",
        "when": "review にいる間",
        "result": '`{"approved": bool, "objections": {"<決断 ID>": "<理由>"}, "changes"?: ["<修正依頼>"]}`',
    },
    Role.DISTILL: {
        "name": "蒸留",
        "when": "done になったタスクに、人が答えた未決事項がある時（1 タスク 1 回。`algr distill` の方針はすぐ）",
        "result": '`{"rules": [{"rule", "source"?}], "retire"?: [{"rule_id", "reason", "source"?}]}`',
    },
}


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
    found: list[dict[str, Any]] = field(default_factory=list)  # 見つけた別件


@dataclass
class ImplementResult:
    pr: str | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    returned: dict[str, Any] | None = None
    found: list[dict[str, Any]] = field(default_factory=list)  # 見つけた別件


@dataclass
class ReviewResult:
    approved: bool
    objections: dict[str, str] = field(default_factory=dict)  # 決断 ID → 理由
    changes: list[str] = field(default_factory=list)  # 判断以外の修正依頼


@dataclass
class DistillResult:
    rules: list[dict[str, str]] = field(default_factory=list)
    retire: list[dict[str, str]] = field(default_factory=list)  # 外す提案。外すのは人の accept を通したときだけ


def job_input(
    role: Role, task: Task, rules: list[Rule], agent: dict[str, Any] | None = None, report: dict[str, Any] | None = None
) -> dict[str, Any]:
    """ジョブに添える入力。エージェントは自分で判断ストアを引いてもよいが、ここに今のルールを渡しておく。

    agent は設定の [agents.<役>]（model、max_tokens など）。どう使うかは実行アダプタが決める。
    report は判断ストアの手入れの候補（DecisionStore の report()）。蒸留にだけ渡す。
    """
    data: dict[str, Any] = {"rules": [{"id": r.id, "text": r.text} for r in rules], "agent": dict(agent or {})}
    if role == Role.IMPLEMENT:
        # やり直しは一から作らず、前の PR と指摘から差分だけ直す
        data["previous"] = {"pr": task.pr, "decisions": [asdict(d) for d in task.decisions]}
        data["feedback"] = list(task.feedback)
    if role == Role.REVIEW:
        # 人が答えた未決事項は人のゲートを通っているので、異議の対象にしない。判断がそれに従っているかは確かめる
        data["settled"] = [q.id for q in task.questions if q.by == "human"]
    if role == Role.DISTILL:
        data["questions"] = [asdict(q) for q in task.questions if q.id in task.to_distill]
        data["report"] = dict(report or {})
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


def _found(data: dict[str, Any]) -> list[dict[str, Any]]:
    # 旧い形（"tasks"）で返すエージェントのプロンプトも、別件として受け取る
    found = _texts(data.get("found", data.get("tasks", [])), "title")
    for t in found:
        depends = t.get("depends", [])
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise ResultError("found[].depends must be a list of task IDs")
        if not isinstance(t.get("priority", 0), int):
            raise ResultError("found[].priority must be an integer")
        if not isinstance(t.get("purpose", ""), str):
            raise ResultError("found[].purpose must be a string")
        _texts(t.get("questions", []), "text")
    return found


def parse(role: Role, data: Any) -> IntakeResult | ImplementResult | ReviewResult | DistillResult:
    if not isinstance(data, dict):
        raise ResultError("result must be an object")
    if role == Role.INTAKE:
        fills = [
            Fill(f["question_id"], f["answer"], list(f.get("rule_ids", [])))
            for f in _texts(data.get("fills", []), "question_id", "answer")
        ]
        return IntakeResult(fills, _texts(data.get("questions", []), "text"), _found(data))
    if role == Role.IMPLEMENT:
        if "returned" in data:
            returned = _need(data, "returned", dict)
            _texts([returned], "text")
            return ImplementResult(returned=returned, found=_found(data))
        decisions = _texts(data.get("decisions", []), "text", "answer")
        for d in decisions:
            for key in ("rule_ids", "question_ids"):
                ids = d.get(key, [])
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise ResultError(f"decisions[].{key} must be a list of IDs")
        return ImplementResult(pr=_need(data, "pr", str), decisions=decisions, found=_found(data))
    if role == Role.REVIEW:
        objections = data.get("objections", {})
        if not isinstance(objections, dict):
            raise ResultError("objections must be an object")
        changes = data.get("changes", [])
        if not isinstance(changes, list) or not all(isinstance(c, str) for c in changes):
            raise ResultError("changes must be a list of strings")
        return ReviewResult(bool(_need(data, "approved", bool)), {str(k): str(v) for k, v in objections.items()}, changes)
    if role == Role.DISTILL:
        # source は元になった決断 ID。report の候補から出した提案には無いので、空にする
        rules, retire = _texts(data.get("rules", []), "rule"), _texts(data.get("retire", []), "rule_id", "reason")
        if not all(isinstance(i.get("source", ""), str) for i in [*rules, *retire]):
            raise ResultError("expected source to be a decision ID")
        return DistillResult([{**i, "source": i.get("source", "")} for i in rules], [{**i, "source": i.get("source", "")} for i in retire])
    raise ResultError(f"unknown role: {role}")
