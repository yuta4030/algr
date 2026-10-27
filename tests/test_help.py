"""説明がコードとずれないこと。README の表とヘルプは、コードの横に置いた説明と突き合わせる。"""

import json
import re
import threading
import urllib.request
from pathlib import Path

from algr import diagrams, flow, ui
from algr.agents import ROLE_HELP
from algr.cli import STANCES, commands
from algr.model import Role, State

README = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")


def table(header: str) -> list[list[str]]:
    """README の、header で始まる表の行（見出しと区切りを除く）をセルの並びで返す。"""
    lines = README.split(header + "\n", 1)[1].splitlines()[1:]
    rows = []
    for line in lines:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split(" | ")])
    return rows


def test_every_state_event_role_and_invariant_is_described():
    assert set(flow.STATE_HELP) == set(State)
    assert set(flow.EVENT_HELP) == set(flow.TRANSITIONS)
    assert set(flow.NOTICE_HELP) == set(flow.NOTICES)
    assert set(ROLE_HELP) == set(Role)
    assert all(f.__doc__ for checks in flow.INVARIANTS.values() for f in checks)
    names = {c["name"] for c in commands()}
    assert {name for ops in STANCES.values() for name in ops} <= names


def test_flow_diagram_draws_every_state_and_transition():
    d = diagrams.FLOW_DIAGRAM
    assert {str(s) for s in State} <= {n.id for n in d.nodes}
    drawn: dict[str, set[str]] = {}
    for e in d.edges:
        if e.event:
            assert flow.TRANSITIONS[e.event][1] == State(e.dst), e.event
            drawn.setdefault(e.event, set()).update(e.src)
    # 遷移元は、同じイベントの矢印をすべて合わせて TRANSITIONS と同じ
    assert drawn == {name: {str(s) for s in sources} for name, (sources, _) in flow.TRANSITIONS.items()}


def test_diagram_nodes_stay_inside_the_picture():
    for d in diagrams.DIAGRAMS:
        assert len({n.id for n in d.nodes}) == len(d.nodes), d.id
        for n in d.nodes:
            assert 0 <= n.x - n.w / 2 and n.x + n.w / 2 <= d.width, (d.id, n.id)
            assert 40 <= n.y - n.h / 2 and n.y + n.h / 2 <= d.height - 30, (d.id, n.id)
        assert d.kinds_used() <= {diagrams.HUMAN, diagrams.AGENT, diagrams.DATA, diagrams.STATE, diagrams.PLAIN}, d.id


def test_readme_event_table_matches_the_code():
    rows = table("| イベント | ペイロード | 既定の処理 |")
    assert [r[0].strip("`") for r in rows] == list(flow.TRANSITIONS)
    for name, payload, handling in rows:
        name = name.strip("`")
        assert handling == flow.EVENT_HELP[name], name
        keys = flow.EVENT_DATA[name]
        assert set(keys) <= set(re.findall(r"`([a-z_]+)`", payload)) if keys else payload == "—", name


def test_readme_notice_table_matches_the_code():
    rows = table("| イベント | ペイロード | 中身 |")
    assert [r[0].strip("`") for r in rows] == list(flow.NOTICES)
    for name, payload, help in rows:
        name = name.strip("`")
        assert help == flow.NOTICE_HELP[name], name
        assert set(flow.NOTICES[name]) <= set(re.findall(r"`([a-z_]+)`", payload)), name


def test_readme_role_table_matches_the_code():
    rows = table("| 役 | 起動する時 | 結果（JSON） |")
    assert {r[0].strip("`"): {"when": r[1], "result": r[2]} for r in rows} == {
        str(role): {"when": h["when"], "result": h["result"]} for role, h in ROLE_HELP.items()
    }


def test_readme_stances_match_the_code():
    rows = table("| 立場 | やること | 答える判断 | 操作 |")
    # `algr accept` / `decline` のように、2 つ目からは algr を省いて書く
    ops = {r[0]: tuple(a or b for a, b in re.findall(r"`algr (\w+)`|/ `(\w+)`", r[3])) for r in rows}
    assert ops == STANCES


def test_help_is_served_from_the_code(repo):
    from algr.config import Config
    from algr.engine import Engine

    httpd = ui.server(Engine.from_config(Config.load(repo)), port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/help") as res:
            h = json.load(res)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert [s["state"] for s in h["states"]] == [str(s) for s in State]
    ready = next(e for e in h["events"] if e["name"] == "task.ready")
    assert (ready["from"], ready["to"]) == (["intake"], "ready")
    assert ready["invariants"] == ["未決事項がゼロであること。", "ルールで埋めた未決事項には、従ったルールの ID が付いていること（判断には根拠を付ける）。"]
    assert [r["role"] for r in h["roles"]] == [str(r) for r in Role]
    assert all(r["prompt"].startswith("# 役：") for r in h["roles"])
    assert set(h["diagrams"]) == {"layers", "flow", "meta"}
    assert all(svg.startswith('<svg class="dg"') for svg in h["diagrams"].values())
