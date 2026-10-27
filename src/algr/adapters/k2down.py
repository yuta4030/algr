"""k2down の判断ストアアダプタ。k2down の CLI を呼んで、ルールの提示と判断の記録を行う。

k2down は https://github.com/yuta4030/k2down 。`uv tool install git+https://github.com/yuta4030/k2down` で入る（`@v0.4.0` のように版を固定できる）。
k2down 自身は判定せず記録と集計だけを行うので、algr のコアと同じく判断はエージェントに任せられる。

ルールの追加と外しは `k2down propose` で提案として積み、人が accept したときに k2down が rules.json に書く。
rules.json の差分は PR で見えるので、そのマージをもって人の確認とする。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from algr.adapters import REPORT_KEYS, AdapterError, Context, Rule

# 提案の理由（propose --reason）と、report --json の declined_proposals の reason / decline_reason の形を使う
MIN_VERSION = (0, 4, 0)


class K2downError(AdapterError):
    pass


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _error(text: str) -> str:
    """--json のときの k2down のエラー（stderr の {"error": ...}）は、文言だけにする。"""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    return data["error"] if isinstance(data, dict) and isinstance(data.get("error"), str) else text


def _decision(source: str) -> list[str]:
    """提案の元になった決断 ID。report の候補から出した提案には無い。"""
    return ["--decision", source] if source else []


class K2down:
    def __init__(
        self,
        ctx: Context,
        dir: str = ".algr/decisions",
        command: list[str] | None = None,
    ) -> None:
        self.dir = ctx.path(dir)
        self.command = list(command) if command else ["k2down"]
        self._checked = False

    def _check_version(self) -> None:
        """最初に k2down を呼ぶときに 1 度だけ、MIN_VERSION 以降かを確かめる。"""
        if self._checked:
            return
        out = self._call([*self.command, "--version"], "--version")
        found = tuple(int(n) for n in re.findall(r"\d+", out.split()[-1])[:3]) if out else ()
        if found < MIN_VERSION:
            need = ".".join(map(str, MIN_VERSION))
            raise K2downError(f"k2down {need} or later is required (found {out or 'unknown'}): uv tool install --force git+https://github.com/yuta4030/k2down")
        self._checked = True

    def _run(self, *args: str) -> str:
        self._check_version()
        return self._call([*self.command, "--dir", str(self.dir), *args], args[0])

    def _call(self, argv: list[str], name: str) -> str:
        # Windows では Python の標準出力が既定で cp932 になるので、k2down に UTF-8 で出させる
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        except FileNotFoundError as e:
            raise K2downError(f"k2down is not installed ({self.command[0]}): uv tool install git+https://github.com/yuta4030/k2down") from e
        if proc.returncode != 0:
            raise K2downError(f"{name}: {_error(proc.stderr.strip() or proc.stdout.strip())}")
        return proc.stdout.strip()

    def rules(self, scope: str | None = None) -> list[Rule]:
        if not (self.dir / "rules.json").exists():
            return []
        # --scope を付けると、k2down がその範囲のルールと範囲の無いルールを返す
        data = json.loads(self._run("list", "--json", *(["--scope", scope] if scope else [])))
        return [Rule(r["id"], r["rule"]) for r in data["rules"] if r["id"].startswith("R")]

    def apply(self, decision: str, problem: str, rule_ids: list[str]) -> None:
        if rule_ids:
            self._run("apply", "--decision", decision, "--problem", _one_line(problem), *rule_ids)

    def reject(self, decision: str, problem: str, rule_ids: list[str]) -> None:
        if rule_ids:
            self._run("reject", "--decision", decision, "--problem", _one_line(problem), *rule_ids)

    def escalate(self, decision: str, problem: str, reason: str, rule_ids: list[str] | None = None) -> None:
        self._run("escalate", "--decision", decision, "--problem", _one_line(problem), "--reason", reason, *(rule_ids or []))

    def decide(self, decision: str, outcome: str, actual: str, predicted: str | None = None) -> None:
        args = ["decide", "--decision", decision, "--outcome", outcome, "--actual", _one_line(actual)]
        if predicted is not None:
            args += ["--predicted", _one_line(predicted)]
        self._run(*args)

    def report(self) -> dict[str, Any]:
        if not (self.dir / "rules.json").exists():
            return {}
        data = json.loads(self._run("report", "--json"))
        rules = [{k: r[k] for k in ("id", "applied", "hit", "miss", "rejected", "decisions")} for r in data.get("rules", [])]
        return {**{k: data.get(k, []) for k in REPORT_KEYS}, "rules": rules}

    # --- 提案（k2down の propose / proposals / accept / decline） ---------------

    def propose(self, rule: str, source: str, scope: str = "") -> str:
        args = ["propose", "add", _one_line(rule), *_decision(source), *(["--scope", scope] if scope else []), "--json"]
        return json.loads(self._run(*args))["id"]

    def propose_retire(self, rule_id: str, reason: str, source: str) -> str:
        args = ["propose", "retire", rule_id, *_decision(source), "--reason", _one_line(reason), "--json"]
        return json.loads(self._run(*args))["id"]

    def proposals(self) -> list[dict[str, str]]:
        if not (self.dir / "proposals.json").exists():
            return []
        out = []
        for p in json.loads(self._run("proposals", "--json"))["proposals"]:
            p.setdefault("decision", "")
            if p["kind"] == "retire":
                p.setdefault("reason", "")
            out.append(p)
        return out

    def accept(self, proposal_id: str) -> str:
        return json.loads(self._run("accept", proposal_id, "--json"))["accepted"][0]["rule_id"]

    def reject_proposal(self, proposal_id: str) -> None:
        self._run("decline", proposal_id)
