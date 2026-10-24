"""LocalProcess から起動される、テスト用のエージェント。

実装の役だけ、タスクの題名で振る舞いを変える。ほかの役は algr.fake_agent に任せる。
- "fail" を含む：何もせず終了コード 1
- "badresult" を含む：約束の形になっていない結果を返す
- "needs-db" を含み、db の判断がまだ無い：起票に差し戻す
- "guess" を含み、naming の判断がまだ無い：ルール無しで判断して PR を出す
- "cite" を含む：R001 に従った判断を載せて PR を出す
- "slow" を含む：結果を出す前に、ファイル stop ができるまで待つ
- "flaky" を含む：ファイル flaky-ok ができるまで終了コード 1
- "fixme" を含む：修正依頼（feedback）を受けてから出した PR は "-fixed" で終わる

レビューの役も、題名で振る舞いを変える。
- "fixme" を含む：PR が "-fixed" で終わるまで修正を依頼する
- "nitpick" を含む：いつも修正を依頼する
- "contrarian" を含む：すべての未決事項に異議を出す
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


if job["role"] == "review" and "fixme" in title and not task["pr"].endswith("-fixed"):
    write({"approved": False, "changes": ["tests fail"]})
elif job["role"] == "review" and "nitpick" in title:
    write({"approved": False, "changes": ["rename this"]})
elif job["role"] == "review" and "contrarian" in title:
    write({"approved": False, "objections": {q["id"]: "disagree" for q in task["questions"]}})
elif job["role"] != "implement":
    write(fake_agent.run(job))
elif "flaky" in title and not Path("flaky-ok").exists():
    sys.exit(1)
elif "fixme" in title and job["input"]["feedback"]:
    assert job["input"]["previous"]["pr"] == f"pr/{task['id']}"  # 前の PR から差分だけ直す
    write({"pr": f"pr/{task['id']}-fixed", "decisions": []})
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
