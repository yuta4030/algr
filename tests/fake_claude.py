"""テスト用の Claude Code（claude -p --output-format json）。

プロンプトの最後の ```json ブロックからジョブを読み、algr.fake_agent の決まりで答える。
受け取った引数とプロンプトは FAKE_CLAUDE_LOG のディレクトリに残す。
"""

import json
import os
import re
import sys
from pathlib import Path

from algr import fake_agent

prompt = sys.stdin.buffer.read().decode("utf-8")
job = json.loads(re.findall(r"```json\s*\n(.*?)\n```", prompt, re.DOTALL)[-1])
log = Path(os.environ["FAKE_CLAUDE_LOG"])
log.mkdir(parents=True, exist_ok=True)
(log / f"{job['id']}.prompt.md").write_text(prompt, encoding="utf-8")
(log / f"{job['id']}.argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")

if "agent-error" in job["task"]["title"]:
    print(json.dumps({"type": "result", "is_error": True, "result": "budget exceeded"}))
    sys.exit(0)
if "auth-error" in job["task"]["title"]:
    # 認証の期限切れ：終了コード 1 で、原因は標準出力の JSON にだけ書かれる
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "OAuth token has expired"}))
    sys.exit(1)
result = fake_agent.run(job)
answer = "結果です。\n\n```json\n" + json.dumps(result, ensure_ascii=False) + "\n```\n"
usage = {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}
print(json.dumps({"type": "result", "is_error": False, "result": answer, "usage": usage, "num_turns": 4}))
