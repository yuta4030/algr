"""本物のエージェントを走らせるコマンド。Claude Code（`claude -p`）に役のプロンプトとジョブを渡し、結果を書き戻す。

fake.process の command に ["python", "-m", "algr.agent"] を書くと使える。ALGR_JOB_FILE を読み、ALGR_RESULT_FILE に書く。

プロンプトは、変わりにくいものから順に並べる（共通部分を先頭に置き、キャッシュを効かせる）。
1. algr 共通（src/algr/prompts/common.md）
2. 役（src/algr/prompts/<役>.md）
3. 利用側の共通と役の追記（.algr/prompts/common.md、.algr/prompts/<役>.md）。社内固有のことはここに書く
4. ジョブ（タスクの要約とルール）

役ごとの設定（[agents.<役>]、ジョブの input.agent）：
- command：Claude Code のコマンド（既定 ["claude"]）
- model：--model
- max_budget_usd：--max-budget-usd
- permission_mode：--permission-mode
- args：そのまま足す引数
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROMPTS = Path(__file__).with_name("prompts")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


class AgentError(Exception):
    pass


def build_prompt(job: dict[str, Any], root: Path) -> str:
    role = job["role"]
    parts = [(PROMPTS / "common.md").read_text(encoding="utf-8"), (PROMPTS / f"{role}.md").read_text(encoding="utf-8")]
    for name in ("common.md", f"{role}.md"):
        local = root / ".algr" / "prompts" / name
        if local.exists():
            parts.append(local.read_text(encoding="utf-8"))
    payload = {"id": job["id"], "role": role, "task": job["task"], "input": {k: v for k, v in job["input"].items() if k != "agent"}}
    parts.append("# ジョブ\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n")
    return "\n\n".join(p.strip() for p in parts) + "\n"


def extract(text: str) -> dict[str, Any]:
    """エージェントの最後の発言から結果の JSON を取り出す。最後の ```json ブロック、無ければ全体を JSON として読む。"""
    blocks = JSON_BLOCK.findall(text)
    candidate = blocks[-1] if blocks else text.strip()
    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise AgentError(f"no JSON result in the agent's answer: {e}") from e
    if not isinstance(result, dict):
        raise AgentError("the agent's result is not a JSON object")
    return result


def command(agent: dict[str, Any]) -> list[str]:
    argv = [*agent.get("command", ["claude"]), "-p", "--output-format", "json", "--no-session-persistence"]
    if "model" in agent:
        argv += ["--model", str(agent["model"])]
    if "max_budget_usd" in agent:
        argv += ["--max-budget-usd", str(agent["max_budget_usd"])]
    if "permission_mode" in agent:
        argv += ["--permission-mode", str(agent["permission_mode"])]
    return argv + [str(a) for a in agent.get("args", [])]


def run(job: dict[str, Any], root: Path) -> dict[str, Any]:
    agent = job["input"].get("agent", {})
    proc = subprocess.run(command(agent), input=build_prompt(job, root), capture_output=True, text=True, cwd=root, check=False)
    if proc.returncode != 0:
        raise AgentError(f"claude exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AgentError(f"claude did not print JSON: {e}") from e
    if out.get("is_error"):
        raise AgentError(f"claude reported an error: {str(out.get('result', ''))[-500:]}")
    result = extract(str(out.get("result", "")))
    usage = out.get("usage") or {}
    result["usage"] = {
        "input_tokens": sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }
    return result


def main() -> int:
    with open(os.environ["ALGR_JOB_FILE"], encoding="utf-8") as f:
        job = json.load(f)
    try:
        result = run(job, Path.cwd())
    except AgentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with open(os.environ["ALGR_RESULT_FILE"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
