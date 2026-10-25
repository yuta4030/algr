"""k2down の判断ストアアダプタ。k2down の CLI を呼んで、ルールの提示と判断の記録を行う。

k2down は https://github.com/yuta4030/k2down 。`uv tool install git+https://github.com/yuta4030/k2down` で入る。
k2down 自身は判定せず記録と集計だけを行うので、algr のコアと同じく判断はエージェントに任せられる。

ルールの追加と外しは `k2down propose` で提案として積み、人が accept したときに k2down が rules.json に書く。
rules.json の差分は PR で見えるので、そのマージをもって人の確認とする。
"""

from __future__ import annotations

import json
import os
import subprocess

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
    ) -> None:
        self.dir = ctx.path(dir)
        self.command = list(command) if command else ["k2down"]

    def _run(self, *args: str) -> str:
        argv = [*self.command, "--dir", str(self.dir), *args]
        # Windows では Python の標準出力が既定で cp932 になるので、k2down に UTF-8 で出させる
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env, check=False)
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

    # --- 提案（k2down の propose / proposals / accept / decline） ---------------

    def propose(self, rule: str, source: str) -> str:
        return json.loads(self._run("propose", "add", _one_line(rule), "--decision", source, "--json"))["id"]

    def propose_retire(self, rule_id: str, reason: str, source: str) -> str:
        # k2down の外す提案には理由の項目が無いので、ID で表せない出どころ（--source）に理由を入れる
        args = ["propose", "retire", rule_id, "--decision", source, "--source", _one_line(reason), "--json"]
        return json.loads(self._run(*args))["id"]

    def proposals(self) -> list[dict[str, str]]:
        if not (self.dir / "proposals.json").exists():
            return []
        out = []
        for p in json.loads(self._run("proposals", "--json"))["proposals"]:
            if p["kind"] == "retire":
                p["reason"] = p.pop("source", "")
            out.append(p)
        return out

    def accept(self, proposal_id: str) -> str:
        return json.loads(self._run("accept", proposal_id, "--json"))["accepted"][0]["rule_id"]

    def reject_proposal(self, proposal_id: str) -> None:
        self._run("decline", proposal_id)
