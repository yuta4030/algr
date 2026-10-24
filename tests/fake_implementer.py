"""LocalProcess から起動される、実装エージェントの代わり。

タスクの題名で振る舞いを変える。
- "fail" を含む：何もせず終了コード 1
- "needs-db" を含み、db の判断がまだ無い：起票に差し戻す
- "guess" を含み、naming の判断がまだ無い：ルール無しで判断して PR を出す
- それ以外：PR を出す
"""

import json
import os
import sys

with open(os.environ["ALGR_TASK_FILE"], encoding="utf-8") as f:
    task = json.load(f)
decided = {q["topic"] for q in task["questions"] if q["answer"] is not None}
title = task["title"]


def write(result):
    with open(os.environ["ALGR_RESULT_FILE"], "w", encoding="utf-8") as f:
        json.dump(result, f)


if "fail" in title:
    sys.exit(1)
if "needs-db" in title and "db" not in decided:
    write({"returned": {"text": "Which database?", "topic": "db", "recommended": "sqlite"}})
elif "guess" in title and "naming" not in decided:
    write({"pr": f"pr/{task['id']}", "decisions": [{"text": "Module name", "topic": "naming", "answer": "util"}]})
else:
    write({"pr": f"pr/{task['id']}", "decisions": []})
