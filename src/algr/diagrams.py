"""ヘルプの図。箱と矢印をデータで持ち、SVG に書き出す。

座標は手で置く。状態とイベントの図は flow.TRANSITIONS と突き合わせる（tests/test_help.py）ので、
状態やイベントを足したら、ここに箱と矢印を足す。見た目は algr ui で確かめる。

見た目の約束：人はオレンジの箱、AI のエージェントは青の箱（右上に「人」「AI」の札）、データは灰色の円柱。
レイヤーは帯で囲む。色は ui.html のトークン（.dg のクラス）から取る。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

HUMAN, AGENT, DATA, STATE, PLAIN = "human", "agent", "data", "state", "plain"
FLOW, WRITE, READ = "flow", "write", "read"  # 矢印の種類：流れ、判断の書き込み、ルールの参照
TAGS = {HUMAN: "人", AGENT: "AI"}


@dataclass
class Node:
    id: str
    x: float  # 中心
    y: float
    w: float
    title: str
    sub: str = ""
    kind: str = STATE
    h: float = 56


@dataclass
class Edge:
    d: str  # SVG の path
    kind: str = FLOW
    event: str = ""  # 状態とイベントの図だけ。突き合わせに使う
    src: tuple[str, ...] = ()
    dst: str = ""
    dot: tuple[float, float] | None = None  # イベントの点
    arrow: bool = True  # 合流する手前の線は矢印を付けない


@dataclass
class Text:
    x: float
    y: float
    text: str
    cls: str = "q"
    anchor: str = "start"


@dataclass
class Band:
    y: float
    h: float
    label: str


@dataclass
class Diagram:
    id: str
    title: str
    width: float
    height: float
    nodes: list[Node]
    edges: list[Edge]
    texts: list[Text] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    legend: list[str] = field(default_factory=list)  # 凡例に出す種類（HUMAN, AGENT, DATA, WRITE, READ, "event"）

    def kinds_used(self) -> set[str]:
        return {n.kind for n in self.nodes}


def _text(t: Text) -> str:
    anchor = f' text-anchor="{t.anchor}"' if t.anchor != "start" else ""
    return f'<text x="{t.x:g}" y="{t.y:g}"{anchor} class="{t.cls}">{escape(t.text)}</text>'


def _shape(kind: str, x: float, y: float, w: float, h: float) -> str:
    """左上 (x, y) から w × h の箱。データは円柱にする。"""
    if kind == DATA:
        ry = 7
        body = f"M{x:g} {y + ry:g}A{w / 2:g} {ry} 0 0 1 {x + w:g} {y + ry:g}V{y + h - ry:g}A{w / 2:g} {ry} 0 0 1 {x:g} {y + h - ry:g}Z"
        rim = f"M{x:g} {y + ry:g}A{w / 2:g} {ry} 0 0 0 {x + w:g} {y + ry:g}"
        return f'<path d="{body}" class="data"/><path d="{rim}" class="rim"/>'
    return f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="8" class="{kind}"/>'


def _node(n: Node) -> str:
    x, y = n.x - n.w / 2, n.y - n.h / 2
    out = [_shape(n.kind, x, y, n.w, n.h)]
    dy = 4 if n.kind == DATA else 0  # 円柱は上の縁の分だけ下げる
    if n.sub:
        out += [_text(Text(n.x, n.y - 4 + dy, n.title, "b", "middle")), _text(Text(n.x, n.y + 14 + dy, n.sub, "q", "middle"))]
    else:
        out.append(_text(Text(n.x, n.y + 5 + dy, n.title, "b", "middle")))
    if n.kind in TAGS:
        tx, ty = x + n.w - 30, y - 8
        out.append(f'<rect x="{tx:g}" y="{ty:g}" width="24" height="15" rx="7.5" class="tag {n.kind}"/>')
        out.append(_text(Text(tx + 12, ty + 11, TAGS[n.kind], "tagt", "middle")))
    return "".join(out)


def _legend(kinds: list[str], y: float) -> str:
    out, x = [], 24.0
    for kind in kinds:
        if kind in (HUMAN, AGENT, DATA):
            out.append(_shape(kind, x, y - 11, 22, 16) if kind == DATA else f'<rect x="{x:g}" y="{y - 10:g}" width="18" height="14" rx="3" class="{kind}"/>')
            label = {HUMAN: "人", AGENT: "AI のエージェント", DATA: "データ"}[kind]
            out.append(_text(Text(x + 28, y + 1, label)))
            x += 40 + 12 * len(label)
        elif kind in (WRITE, READ, FLOW):
            out.append(f'<line x1="{x:g}" y1="{y - 3:g}" x2="{x + 30:g}" y2="{y - 3:g}" class="ln {kind}"/>')
            label = {WRITE: "判断の書き込み", READ: "ルールの参照", FLOW: "流れ"}[kind]
            out.append(_text(Text(x + 38, y + 1, label)))
            x += 50 + 12 * len(label)
        elif kind == "event":
            out.append(f'<circle cx="{x + 5:g}" cy="{y - 3:g}" r="4" class="dot"/>')
            out.append(_text(Text(x + 16, y + 1, "イベント（太字が名前、灰色が既定の処理）")))
    return "".join(out)


def _marker(mid: str, cls: str) -> str:
    return (
        f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0 0L10 5L0 10z" class="{cls}"/></marker>'
    )


def render(d: Diagram) -> str:
    out = [
        f'<svg class="dg" viewBox="0 0 {d.width:g} {d.height:g}" role="img" aria-label="{escape(d.title)}">',
        f"<defs>{_marker(d.id + '-a', 'mk')}{_marker(d.id + '-r', 'mk read')}</defs>",
        _text(Text(24, 34, d.title, "title")),
    ]
    for b in d.bands:
        out.append(f'<rect x="12" y="{b.y:g}" width="{d.width - 24:g}" height="{b.h:g}" rx="12" class="band"/>')
        out.append(_text(Text(26, b.y + b.h - 10, b.label, "q b")))
    for e in d.edges:
        marker = f' marker-end="url(#{d.id}-{"r" if e.kind == READ else "a"})"' if e.arrow else ""
        out.append(f'<path d="{e.d}" class="ln {e.kind}"{marker}/>')
    out += [_node(n) for n in d.nodes]
    out += [f'<circle cx="{e.dot[0]:g}" cy="{e.dot[1]:g}" r="4" class="dot"/>' for e in d.edges if e.dot]
    out += [_text(t) for t in d.texts]
    if d.legend:
        out.append(_legend(d.legend, d.height - 16))
    out.append("</svg>")
    return "".join(out)


# --- 2 つの層 -----------------------------------------------------------------

LAYERS = Diagram(
    id="layers",
    title="処理の層でタスクが流れ、メタの層で判断がルールに育つ",
    width=760,
    height=600,
    bands=[Band(56, 200, "処理の層（STP）"), Band(272, 104, "データ"), Band(392, 160, "メタの層（アルゴリズミック）")],
    nodes=[
        Node("client", 380, 104, 520, "クライアント", "やることと目的を出し、決まらない点に答える", HUMAN, 48),
        Node("intake", 180, 200, 160, "起票エージェント", "未決事項をルールで埋める", AGENT),
        Node("implement", 380, 200, 160, "実装エージェント", "ready で着手し PR を出す", AGENT),
        Node("review", 580, 200, 160, "レビューエージェント", "通ればマージへ", AGENT),
        Node("decisions", 320, 320, 440, "判断", "人の答えも、ルールに従った判断も残る（k2down）", DATA, 60),
        Node("rules", 660, 320, 140, "ルール", "rules.json", DATA, 60),
        Node("distill", 200, 460, 160, "蒸留エージェント", "done のタスクから提案", AGENT),
        Node("meta", 440, 460, 180, "メタプログラマー", "提案を accept / decline", HUMAN),
    ],
    edges=[
        Edge("M160 128V172"),  # 依頼・答え
        Edge("M200 172V128"),  # needs-decision
        Edge("M380 172V128"),  # 想定外の判断
        Edge("M580 172V128"),  # 異議・マージ待ち
        Edge("M24 214H100"),  # CI の失敗・派生タスク
        Edge("M260 200H300"),
        Edge("M460 200H500"),
        Edge("M180 228V290", WRITE),
        Edge("M380 228V290", WRITE),
        Edge("M520 228V290", WRITE),
        Edge("M660 290V256", READ),  # ルール → 処理の層
        Edge("M200 350V432"),  # 判断 → 蒸留
        Edge("M440 350V432"),  # 判断 → メタプログラマー（report で見直す）
        Edge("M280 460H350"),  # 提案
        Edge("M530 460H660V350"),  # accept
    ],
    texts=[
        Text(24, 184, "CI の失敗・"),
        Text(24, 200, "派生タスク"),
        Text(152, 154, "依頼・答え", anchor="end"),
        Text(208, 154, "needs-decision"),
        Text(388, 154, "想定外の判断"),
        Text(588, 154, "異議・マージ待ち"),
        Text(280, 192, "ready", anchor="middle"),
        Text(480, 192, "PR", anchor="middle"),
        Text(652, 280, "毎ジョブの入力", anchor="end"),
        Text(208, 396, "人の答えを読む"),
        Text(448, 396, "記録から見直す（report）"),
        Text(315, 452, "提案", anchor="middle"),
        Text(596, 452, "accept", anchor="middle"),
    ],
    legend=[HUMAN, AGENT, DATA, WRITE, READ],
)


# --- 状態とイベント -----------------------------------------------------------

FLOW_DIAGRAM = Diagram(
    id="flow",
    title="状態は 8 つ。箱の色はその状態で誰の手にあるか、点はイベント（hook の差し込み口）",
    width=760,
    height=900,
    nodes=[
        Node("by-human", 190, 84, 200, "クライアントが依頼", "algr new", HUMAN),
        Node("by-agent", 410, 84, 200, "エージェントが依頼", "CI の失敗・派生タスク", AGENT),
        Node("intake", 300, 200, 200, "intake", "起票エージェント", AGENT),
        Node("needs-decision", 650, 330, 200, "needs-decision", "人が答える（algr answer）", HUMAN),
        Node("ready", 300, 330, 200, "ready", "配車待ち（依存先を待つ）"),
        Node("in-progress", 300, 460, 200, "in-progress", "実装エージェント", AGENT),
        Node("review", 300, 590, 200, "review", "レビューエージェント", AGENT),
        Node("approved", 560, 720, 180, "approved", "人のマージ待ち（ゲート）", HUMAN),
        Node("done", 300, 830, 200, "done", "マージ済み → 蒸留エージェント"),
        Node("failed", 95, 720, 150, "failed", "人が reopen する", HUMAN),
    ],
    edges=[
        Edge("M190 112V130H300", arrow=False),
        Edge("M410 112V130H300V172", event="task.created", dst="intake", dot=(300, 152)),
        Edge("M300 228V302", event="task.ready", src=("intake",), dst="ready", dot=(300, 265)),
        Edge("M300 358V432", event="task.started", src=("ready",), dst="in-progress", dot=(300, 395)),
        Edge("M300 488V562", event="task.submitted", src=("in-progress",), dst="review", dot=(300, 525)),
        Edge("M300 618V802", event="task.done", src=("review",), dst="done", dot=(300, 720)),
        Edge("M400 190H650V302", event="decision.requested", src=("intake",), dst="needs-decision", dot=(560, 190)),
        Edge("M550 340H470V214H400", event="decision.resolved", src=("needs-decision",), dst="intake", dot=(470, 270)),
        Edge("M400 576H690V358", event="review.escalated", src=("review",), dst="needs-decision", dot=(690, 470)),
        Edge("M360 562V488", event="review.changes_requested", src=("review",), dst="in-progress", dot=(360, 525)),
        Edge("M200 470H130V214H200", event="task.returned", src=("in-progress",), dst="intake", dot=(130, 420)),
        Edge("M400 604H560V692", event="review.approved", src=("review",), dst="approved", dot=(480, 604)),
        Edge("M560 748V830H400", event="task.done", src=("approved",), dst="done", dot=(560, 790)),
        Edge(
            "M200 600H95V692",
            event="task.failed",
            src=("intake", "in-progress", "review", "approved"),  # 図では review から 1 本で描く
            dst="failed",
            dot=(150, 600),
        ),
        Edge("M50 692V186H200", event="task.reopened", src=("failed",), dst="intake", dot=(50, 640)),
    ],
    texts=[
        Text(288, 156, "task.created", "b s", "end"),
        Text(288, 171, "起票エージェントを起動", anchor="end"),
        Text(312, 262, "task.ready", "b s"),
        Text(312, 277, "配車を待つ"),
        Text(312, 392, "task.started", "b s"),
        Text(312, 407, "実装エージェントを起動"),
        Text(288, 522, "task.submitted", "b s", "end"),
        Text(288, 537, "レビューを起動", anchor="end"),
        Text(312, 716, "task.done", "b s"),
        Text(312, 731, "マージ（auto）"),
        Text(560, 182, "decision.requested", "b s", "middle"),
        Text(560, 206, "人に知らせる", anchor="middle"),
        Text(480, 266, "decision.resolved", "b s"),
        Text(480, 281, "起票をやり直す"),
        Text(682, 466, "review.escalated", "b s", "end"),
        Text(682, 481, "異議を人に上げる", anchor="end"),
        Text(372, 522, "review.changes_requested", "b s"),
        Text(372, 537, "実装に戻す（max_rework 回まで）"),
        Text(142, 416, "task.returned", "b s"),
        Text(142, 431, "起票に戻す"),
        Text(480, 626, "review.approved", "b s", "middle"),
        Text(480, 641, "マージ待ちを知らせる", anchor="middle"),
        Text(572, 786, "task.done", "b s"),
        Text(572, 801, "algr merge"),
        Text(150, 590, "task.failed", "b s", "middle"),
        Text(140, 618, "作業中の 4 状態から", anchor="middle"),
        Text(62, 646, "task.reopened", "b s"),
    ],
    legend=[HUMAN, AGENT, "event"],
)


# --- ルールを育てる（メタプログラミング） ---------------------------------------

META = Diagram(
    id="meta",
    title="人の答えが提案になり、メタプログラマーが受け入れたものだけがルールになる",
    width=760,
    height=570,
    nodes=[
        Node("answers", 90, 90, 150, "人の答え", "done のタスクの分", DATA, 60),
        Node("policy", 280, 90, 150, "人の方針", "タスクの外で言ったこと", HUMAN),
        Node("distill", 90, 220, 140, "蒸留エージェント", "1 行ルールの提案", AGENT),
        Node("proposals", 280, 220, 140, "提案", "足す・外す", DATA, 60),
        Node("meta", 470, 220, 140, "メタプログラマー", "受け入れるか決める", HUMAN),
        Node("rules", 670, 220, 140, "ルール", "rules.json（PR で入る）", DATA, 60),
        Node("agents", 670, 350, 160, "起票・実装・レビュー", "ルールで判断を埋める", AGENT),
        Node("decisions", 670, 480, 160, "判断の記録", "usage.jsonl", DATA, 60),
        Node("report", 380, 480, 200, "k2down report", "当たり率・落とす／書き直す候補", PLAIN),
    ],
    edges=[
        Edge("M90 120V192"),
        Edge("M280 118V150H130V192"),
        Edge("M160 220H210"),
        Edge("M350 220H400"),
        Edge("M540 220H600"),
        Edge("M670 250V322", READ),
        Edge("M670 378V450", WRITE),
        Edge("M590 480H482"),
        Edge("M440 452V248"),
        Edge("M510 248V420H630V450", WRITE),
    ],
    texts=[
        Text(205, 142, "algr distill（すぐ）", anchor="middle"),
        Text(185, 184, "propose", anchor="middle"),
        Text(375, 184, "algr proposals", anchor="middle"),
        Text(570, 184, "algr accept", anchor="middle"),
        Text(662, 290, "ジョブの入力", anchor="end"),
        Text(678, 410, "従った・異議"),
        Text(678, 425, "当たり"),
        Text(536, 472, "集計", anchor="middle"),
        Text(432, 350, "見直す（言い直す・", anchor="end"),
        Text(432, 365, "外す提案を出す）", anchor="end"),
        Text(518, 330, "algr decline"),
        Text(518, 345, "（却下も残る）"),
    ],
    legend=[HUMAN, AGENT, DATA, WRITE, READ],
)

DIAGRAMS = (LAYERS, FLOW_DIAGRAM, META)


def svgs() -> dict[str, str]:
    return {d.id: render(d) for d in DIAGRAMS}
