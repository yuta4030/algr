"""LocalProcess から起動される、テスト用のエージェント。

実装の役だけ、タスクの題名で振る舞いを変える。ほかの役は algr.fake_agent に任せる。
- "fail" を含む：何もせず終了コード 1
- "badresult" を含む：約束の形になっていない結果を返す
- "needs-db" を含み、db の判断がまだ無い：起票に差し戻す
- "guess" を含み、naming の判断がまだ無い：ルール無しで判断して PR を出す
- "cite" を含む：R001 に従った判断を載せて PR を出す
- "slow" を含む：結果を出す前に、ファイル stop ができるまで待つ
"""

import json
import os
import sys
import time
from pathlib import Path

from algr import fake_agent

with open(os.environ["ALGR_JOB_FILE"], encoding="utf-8") as f:
    job = json.load(f)
task = job["task"]
title = task["title"]
decided = {q["topic"] for q in task["questions"] if q["answer"] is not None}


def write(result):
    with open(os.environ["ALGR_RESULT_FILE"], "w", encoding="utf-8") as f:
        json.dump(result, f)


if job["role"] != "implement":
    write(fake_agent.run(job))
elif "fail" in title:
    sys.exit(1)
elif "badresult" in title:
    write({"decisions": "nope"})
elif "needs-db" in title and "db" not in decided:
    write({"returned": {"text": "Which database?", "topic": "db", "recommended": "sqlite"}})
elif "guess" in title and "naming" not in decided:
    write({"pr": f"pr/{task['id']}", "decisions": [{"text": "Module name", "topic": "naming", "answer": "util"}]})
elif "cite" in title:
    write({"pr": f"pr/{task['id']}", "decisions": [{"text": "Auth", "topic": "auth", "answer": "OAuth", "rule_ids": ["R001"]}]})
elif "slow" in title:
    while not Path("stop").exists():
        time.sleep(0.02)
    write({"pr": f"pr/{task['id']}", "decisions": []})
else:
    write({"pr": f"pr/{task['id']}", "decisions": []})
