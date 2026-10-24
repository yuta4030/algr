"""k2down の判断ストアアダプタ。k2down の CLI を呼んで、ルールの提示と判断の記録を行う。

k2down は https://github.com/yuta4030/k2down 。`uv tool install git+https://github.com/yuta4030/k2down` で入る。
k2down 自身は判定せず記録と集計だけを行うので、algr のコアと同じく判断はエージェントに任せられる。

ルールの追加は提案として .algr/state/proposals.json に積み、人が accept したときに `k2down add` で rules.json に書く。
rules.json の差分は PR で見えるので、そのマージをもって人の確認とする。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from algr.adapters import AdapterError, Context, Rule


class K2downError(AdapterError):
    pass


def _one_line(text: str) -> str:
    return " ".join(text.split())


class K2down:
    def __init__(
        self,
        ctx: Context,
        dir: str = ".algr/decisions",
        command: list[str] | None = None,
        proposals: str = ".algr/state/proposals.json",
    ) -> None:
        self.dir = ctx.path(dir)
        self.command = list(command) if command else ["k2down"]
        self.proposals_file = ctx.path(proposals)

    def _run(self, *args: str) -> str:
        argv = [*self.command, "--dir", str(self.dir), *args]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        except FileNotFoundError as e:
            raise K2downError(f"k2down is not installed ({self.command[0]}): uv tool install git+https://github.com/yuta4030/k2down") from e
        if proc.returncode != 0:
            raise K2downError(f"{' '.join(args[:1])}: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout.strip()

    def rules(self) -> list[Rule]:
        if not (self.dir / "rules.json").exists():
            return []
        data = json.loads(self._run("list", "--json"))
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

    # --- 提案（k2down の外で持つ） -----------------------------------------

    def _load(self) -> list[dict[str, str]]:
        if not self.proposals_file.exists():
            return []
        return json.loads(self.proposals_file.read_text(encoding="utf-8"))

    def _save(self, proposals: list[dict[str, Any]]) -> None:
        self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.proposals_file.with_name(self.proposals_file.name + ".tmp")
        tmp.write_text(json.dumps(proposals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.proposals_file)

    def propose(self, rule: str, source: str) -> str:
        proposals = self._load()
        for p in proposals:
            if p["rule"] == rule:
                return p["id"]  # 冪等：同じ提案は二重に出さない
        numbers = [int(p["id"][1:]) for p in proposals]
        proposal_id = f"P{max(numbers, default=0) + 1:03d}"
        proposals.append({"id": proposal_id, "rule": _one_line(rule), "source": source})
        self._save(proposals)
        return proposal_id

    def proposals(self) -> list[dict[str, str]]:
        return self._load()

    def accept(self, proposal_id: str) -> str:
        proposals = self._load()
        proposal = next((p for p in proposals if p["id"] == proposal_id), None)
        if proposal is None:
            raise KeyError(f"no such proposal: {proposal_id}")
        self.dir.mkdir(parents=True, exist_ok=True)
        rule_id = self._run("add", proposal["rule"], "--source", proposal["source"])
        self._save([p for p in proposals if p["id"] != proposal_id])
        return rule_id

    def reject_proposal(self, proposal_id: str) -> None:
        proposals = self._load()
        if not any(p["id"] == proposal_id for p in proposals):
            raise KeyError(f"no such proposal: {proposal_id}")
        self._save([p for p in proposals if p["id"] != proposal_id])
