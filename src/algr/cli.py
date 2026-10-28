"""algr のコマンドライン。

クライアントは起票し、判断待ちの未決事項に答える。メタプログラマーはルールを直す。それ以外はフローが流す。
既定の出力は LLM にも読みやすいプレーンテキスト。--json で JSON を返す。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from algr import __version__, flow, stats, ui
from algr.adapters import AdapterError, Conflict
from algr.config import CONFIG_FILE, Config, ConfigError
from algr.engine import Engine
from algr.flow import TransitionError
from algr.model import Event, State, Task

DESCRIPTION = """\
algr: a flow for Algorithmic Development.
Rules fill in decisions; ready tasks flow through implementation and review to merge.
The client only sees the waiting states (waiting-intake / waiting-implement / waiting-review) and failed (to reopen).

  algr init                                         # write .algr/config.toml with fake adapters
  algr new "Add login" --question "auth: How do users sign in?"
  algr list --state waiting                         # every task waiting for an answer, whichever role asked
  algr answer I1 I1-1="OAuth"                       # the role that asked runs again with the answer
  algr depends I2 --add I1 --remove I3              # change the tasks I2 waits for
  algr reopen I1 --note "fixed the runner"         # send a failed task back to intake
  algr cancel I1 --note "split into I4 and I5"      # stop a task; to change its purpose, cancel and new
  algr distill "Store dates in UTC" --topic time    # ask the distill agent to propose rules from a policy
  algr tick                                         # take in triggers, collect job results, start jobs
  algr run                                          # tick until no job is running
  algr watch                                        # keep ticking until Ctrl-C (the resident process)
  algr ui                                           # see the flow in a browser (http://127.0.0.1:8765)
"""

# list --state に渡すと、判断待ちの状態（waiting-*）をまとめて拾う
WAITING = "waiting"

# 操作がどちらの立場のものか。README の「立場」と algr ui のヘルプはここと突き合わせる（tests/test_help.py）。
# ここに無いコマンドは、見るためのものと、フローを回すためのもの
STANCES: dict[str, tuple[str, ...]] = {
    "クライアント": ("new", "answer", "depends", "reopen", "cancel"),
    "メタプログラマー": ("distill", "accept", "decline"),
}

INIT_CONFIG = """\
# algr の設定。個人の ~/.algr/config.toml が同じ項目を上書きする。
# use には組み込み名（fake.tasks など）、'pkg.module:Name'、'.algr/adapters/x.py:Name' を書ける。

[flow]
max_parallel = 1  # 同時に in-progress にするタスクの数
max_rework = 2    # レビューの修正依頼で実装に戻す回数の上限。超えたら failed（PJ の修正依頼は数えない）

[adapters.task]
use = "fake.tasks"
dir = ".algr/tasks"

[adapters.trigger]
use = "fake.inbox"

[adapters.executor]
use = "fake.process"
max_parallel = 2  # 起票・実装・レビュー・蒸留のジョブを同時に走らせる数
# command = ["claude", "-p", "..."]  # 省略するとフェイクのエージェント（python -m algr.fake_agent）

[adapters.decisions]
use = "fake.rules"
dir = ".algr/decisions"

[adapters.merge]
use = "fake.merge"  # 頼まれたらすぐマージ済みにする。マージ先（main か統合ブランチか）はアダプタの設定で選ぶ

[adapters.notify]
use = "stub"

[hooks]
# "task.done" = [".algr/hooks.py:on_done"]  # hook(event, engine)。"*" ですべてのイベント
"""


def _task_text(task: Task) -> str:
    lines = [f"{task.id} [{task.state}] {task.title}"]
    if task.purpose:
        lines.append(f"  purpose: {task.purpose}")
    if task.depends:
        lines.append(f"  depends: {' '.join(task.depends)}")
    if task.pr:
        lines.append(f"  pr: {task.pr}")
    for q in task.questions:
        if q.open:
            rec = f" (recommended: {q.recommended})" if q.recommended else ""
            lines.append(f"  ? {q.id} {q.text}{rec}")
        else:
            grounds = f" [{' '.join(q.rule_ids)}]" if q.rule_ids else f" [{q.by}]"
            lines.append(f"  = {q.id} {q.text} -> {q.answer}{grounds}")
    for d in task.decisions:
        grounds = f" [{' '.join(d.rule_ids or d.question_ids)}]" if d.rule_ids or d.question_ids else " [agent]"
        lines.append(f"  * {d.id} {d.text} -> {d.answer}{grounds}")
    for change in task.feedback:
        lines.append(f"  ! {change}")
    return "\n".join(lines)


def _proposal_text(p: dict[str, str]) -> str:
    origin = p["decision"] or "report"
    if p["kind"] == "retire":
        return f"{p['id']} retire {p['rule_id']}: {p['reason']} (from {origin})"
    scope = f" [{', '.join(p['scopes'])}]" if p.get("scopes") else ""
    return f"{p['id']} {p['rule']}{scope} (from {origin})"


def _question(value: str) -> dict[str, str]:
    topic, sep, text = value.partition(": ")
    return {"topic": topic, "text": text} if sep else {"text": value}


def _answer(value: str) -> tuple[str, str]:
    qid, sep, answer = value.partition("=")
    if not sep or not answer:
        raise argparse.ArgumentTypeError(f"expected QUESTION_ID=ANSWER, got {value!r}")
    return qid, answer


def commands() -> list[dict[str, str]]:
    """サブコマンドと、その help と立場（立場の操作でなければ空）。"""
    stance = {name: s for s, names in STANCES.items() for name in names}
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return [{"name": a.dest, "help": a.help or "", "stance": stance.get(a.dest, "")} for a in sub._choices_actions]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algr", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"algr {__version__}")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("-C", "--dir", type=Path, default=None, help="run as if started in this directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="write .algr/config.toml with fake adapters")
    sub.add_parser("config", help="show the merged configuration")

    p = sub.add_parser("new", help="create a task and run intake")
    p.add_argument("title")
    p.add_argument("--purpose", default="")
    p.add_argument("--question", "-q", action="append", default=[], type=_question, help='"topic: text" or "text"')
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--depends", action="append", default=[], metavar="TASK_ID", help="wait until this task is done")
    p.add_argument("--source", choices=["human", "auto"], default="human")

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--state", choices=[WAITING, *(str(s) for s in State)], default=None, help=f"{WAITING}: all waiting-* states")

    p = sub.add_parser("show", help="show a task")
    p.add_argument("task_id")

    p = sub.add_parser("answer", help="answer open questions of a waiting task")
    p.add_argument("task_id")
    p.add_argument("answers", nargs="+", type=_answer, metavar="QUESTION_ID=ANSWER")

    p = sub.add_parser("depends", help="add or remove tasks this task waits for")
    p.add_argument("task_id")
    p.add_argument("--add", action="append", default=[], metavar="TASK_ID", help="wait until this task is done")
    p.add_argument("--remove", action="append", default=[], metavar="TASK_ID", help="stop waiting for this task")

    p = sub.add_parser("reopen", help="send a failed task back to intake")
    p.add_argument("task_id")
    p.add_argument("--note", default="")

    p = sub.add_parser("cancel", help="stop a task that is not done (to change its purpose, cancel it and create a new one)")
    p.add_argument("task_id")
    p.add_argument("--note", default="")

    sub.add_parser("tick", help="take in triggers, collect job results and start jobs once")
    p = sub.add_parser("run", help="tick until no job is running")
    p.add_argument("--interval", type=float, default=0.5, help="seconds between ticks")
    p.add_argument("--timeout", type=float, default=None, help="give up after this many seconds")
    p = sub.add_parser("watch", help="keep ticking: take in triggers, dispatch ready tasks, run jobs (Ctrl-C to stop)")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between ticks")
    sub.add_parser("rules", help="list the rules from the decision store")
    p = sub.add_parser("distill", help="ask the distill agent to turn a policy into rule proposals")
    p.add_argument("policy")
    p.add_argument("--topic", default="")
    sub.add_parser("proposals", help="list rule proposals from the distill agent")
    p = sub.add_parser("accept", help="accept a rule proposal")
    p.add_argument("proposal_id")
    p = sub.add_parser("decline", help="decline a rule proposal")
    p.add_argument("proposal_id")
    sub.add_parser("stats", help="return rates, STP rate, client questions per waiting state, PJ change requests and jobs per role from the event log")
    p = sub.add_parser("ui", help="serve a page that shows tasks, progress and stats on 127.0.0.1 (Ctrl-C to stop)")
    p.add_argument("--port", type=int, default=8765)
    return parser


def run(args: argparse.Namespace) -> Any:
    cwd = (args.dir or Path.cwd()).resolve()
    if args.command == "init":
        path = cwd / ".algr" / CONFIG_FILE
        if path.exists():
            raise ConfigError(f"{path} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(INIT_CONFIG, encoding="utf-8")
        return {"created": str(path)}

    config = Config.load(cwd)
    if args.command == "config":
        return {"root": str(config.ctx.root), "sources": [str(p) for p in config.sources], "config": config.data}

    engine = Engine.from_config(config)
    if args.command == "new":
        return engine.create(
            args.title,
            purpose=args.purpose,
            questions=args.question,
            source=args.source,
            priority=args.priority,
            depends=args.depends,
        )
    if args.command == "list":
        if args.state == WAITING:
            return [t for t in engine.tasks.list() if t.state in flow.WAITING]
        return engine.tasks.list(State(args.state) if args.state else None)
    if args.command == "show":
        return engine.tasks.get(args.task_id)
    if args.command == "answer":
        return engine.resolve(args.task_id, dict(args.answers))
    if args.command == "depends":
        return engine.depend(args.task_id, add=args.add, remove=args.remove)
    if args.command == "reopen":
        return engine.reopen(args.task_id, args.note)
    if args.command == "cancel":
        return engine.cancel(args.task_id, args.note)
    if args.command == "tick":
        return {"events": [e.to_dict() for e in engine.tick()]}
    if args.command == "run":
        return {"events": [e.to_dict() for e in engine.run(interval=args.interval, timeout=args.timeout)]}
    if args.command == "stats":
        return {"stats": stats.summarize(stats.load(engine.event_log))} if engine.event_log else {"stats": {}}
    if args.command == "watch":

        def show(events: list[Event]) -> None:
            for e in events:
                print(json.dumps(e.to_dict(), ensure_ascii=False) if args.json else f"{e.task_id} {e.name}", flush=True)

        engine.watch(interval=args.interval, on_events=show)
        return None
    if args.command == "ui":
        httpd = ui.server(engine, args.port)
        print(f"http://127.0.0.1:{httpd.server_address[1]}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return None
    if args.command == "distill":
        return engine.request_distill(args.policy, topic=args.topic)
    if args.command == "proposals":
        return {"proposals": engine.decisions.proposals()}
    if args.command == "accept":
        return {"rules": [{"id": engine.decisions.accept(args.proposal_id), "text": "accepted " + args.proposal_id}]}
    if args.command == "decline":
        engine.decisions.reject_proposal(args.proposal_id)
        return {"proposals": engine.decisions.proposals()}
    if args.command == "rules":
        return {"rules": [{"id": r.id, "text": r.text, "scopes": list(r.scopes)} for r in engine.decisions.rules()]}
    raise AssertionError(args.command)


def _to_json(value: Any) -> Any:
    if isinstance(value, Task):
        return value.to_dict()
    if isinstance(value, list):
        return [_to_json(v) for v in value]
    return value


def _to_text(value: Any) -> str:
    if isinstance(value, Task):
        return _task_text(value)
    if isinstance(value, list):
        return "\n".join(_task_text(t) for t in value)
    if "events" in value:
        return "\n".join(f"{e['task_id']} {e['name']}" for e in value["events"])
    if "stats" in value:
        return "\n".join(f"{k}: {'-' if v is None else v}" for k, v in value["stats"].items())
    if "proposals" in value:
        return "\n".join(_proposal_text(p) for p in value["proposals"])
    if "rules" in value:
        return "\n".join(f"{r['id']} {r['text']}" + (f" [{', '.join(r['scopes'])}]" if r.get("scopes") else "") for r in value["rules"])
    if "created" in value:
        return f"created {value['created']}"
    return json.dumps(value, ensure_ascii=False, indent=2)


def _tolerate_unencodable() -> None:
    # Windows のコンソール（cp932 など）で出せない文字は ? に置き換え、出力の途中で落とさない
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _tolerate_unencodable()
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (ConfigError, TransitionError, Conflict, AdapterError, KeyError, TimeoutError) as e:
        message = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        if args.json:
            print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1
    if result is None:
        return 0
    if args.json:
        print(json.dumps(_to_json(result), ensure_ascii=False))
    else:
        text = _to_text(result)
        if text:
            print(text)
    return 0
