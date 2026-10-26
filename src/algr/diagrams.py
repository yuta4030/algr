"""ヘルプの図。箱と矢印をデータで持ち、SVG に書き出す。

座標は手で置く。状態とイベントの図は flow.TRANSITIONS と突き合わせる（tests/test_help.py）ので、
状態やイベントを足したら、ここに箱と矢印を足す。見た目は algr ui で確かめる。

見た目の約束：人はオレンジの箱、AI のエージェントは青の箱（右上に「人」「AI」の札）、データは灰色の円柱。
状態は人でも AI でもないので白い箱にし、その状態で手を動かすのが人か AI かを左の線の色で示す。
レイヤーは帯で囲む。図 1 が全体、図 2 が処理の層、図 3 がメタの層の中身で、同じものは同じ名前と色で描く。
色は ui.html のトークン（.dg のクラス）から取る。
"""

from __future__ import annotations

import math
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
    holder: str = ""  # 状態の箱だけ。その状態で手を動かすのが HUMAN か AGENT か


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
    x: float = 12
    w: float = 0  # 0 なら図の幅いっぱい


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
    if n.holder:
        out.append(f'<rect x="{x + 4:g}" y="{y + 8:g}" width="4" height="{n.h - 16:g}" rx="2" class="bar {n.holder}"/>')
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
        elif kind in ("holder-human", "holder-agent"):
            holder = kind.split("-")[1]
            out.append(f'<rect x="{x:g}" y="{y - 10:g}" width="18" height="14" rx="3" class="state"/>')
            out.append(f'<rect x="{x + 2:g}" y="{y - 8:g}" width="3" height="10" rx="1.5" class="bar {holder}"/>')
            label = {"human": "人が手を動かす状態", "agent": "AI が手を動かす状態"}[holder]
            out.append(_text(Text(x + 28, y + 1, label)))
            x += 40 + 12 * len(label)
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
        w = b.w or d.width - 24
        out.append(f'<rect x="{b.x:g}" y="{b.y:g}" width="{w:g}" height="{b.h:g}" rx="12" class="band"/>')
        out.append(_text(Text(b.x + 14, b.y + b.h - 10, b.label, "q b")))
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


# --- 図 1：全体（列が人・AI・データ、行が処理の層とメタの層） ---------------------------

LAYERS = Diagram(
    id="layers",
    title="列が人・AI・データ、行が処理の層とメタの層。データは両方の層で共有する",
    width=760,
    height=530,
    bands=[
        Band(80, 160, "処理の層（STP）→ 図 2", w=488),
        Band(276, 164, "メタの層（アルゴリズミック）→ 図 3", w=488),
        Band(80, 360, "データ", x=528, w=220),
    ],
    nodes=[
        Node("client", 120, 165, 164, "クライアント", "依頼し、問いに答える", HUMAN, 64),
        Node("agents", 382, 165, 200, "起票・実装・レビュー", "ルールで判断を埋めて流す", AGENT, 64),
        Node("meta", 120, 355, 164, "メタプログラマー", "accept・merge・reopen", HUMAN, 64),
        Node("distill", 382, 355, 200, "蒸留エージェント", "人の答えと report から提案", AGENT, 64),
        Node("decisions", 640, 125, 160, "判断", "人の答えも、AI の判断も", DATA),
        Node("rules", 640, 210, 160, "ルール", "rules.json", DATA),
        Node("report", 640, 300, 170, "k2down report", "落とす／書き直す候補", PLAIN, 48),
        Node("proposals", 640, 380, 160, "提案", "足す・外す", DATA),
    ],
    edges=[
        Edge("M202 155H282"),  # 依頼・答え
        Edge("M282 177H202"),  # 問い（needs-decision）
        Edge("M482 140H560", WRITE),  # エージェント → 判断
        Edge("M560 210H420V197", READ),  # ルール → エージェント
        Edge("M720 125H744V300H725"),  # 判断 → report
        Edge("M555 300H420V323"),  # report → 蒸留
        Edge("M482 365H560"),  # 蒸留 → 提案
        Edge("M640 408V470H120V387"),  # 提案 → メタプログラマー
        Edge("M202 345H232V258H600V238"),  # accept → ルール
    ],
    texts=[
        Text(120, 66, "人", "q b", "middle"),
        Text(382, 66, "AI のエージェント", "q b", "middle"),
        Text(638, 66, "データ", "q b", "middle"),
        Text(242, 147, "依頼・答え", anchor="middle"),
        Text(242, 194, "問い", anchor="middle"),
        Text(521, 132, "書く", anchor="middle"),
        Text(490, 203, "入力", anchor="middle"),
        Text(486, 292, "候補を読む", anchor="middle"),
        Text(521, 357, "提案", anchor="middle"),
        Text(380, 463, "algr proposals", anchor="middle"),
        Text(330, 251, "algr accept", anchor="middle"),
    ],
    legend=[HUMAN, AGENT, DATA, WRITE, READ],
)


# --- 図 2：処理の層の中身（状態とイベント） ---------------------------------------

FLOW_DIAGRAM = Diagram(
    id="flow",
    title="処理の層：状態は 8 つ。遷移ごとにイベントが 1 つ出て、hook の差し込み口になる",
    width=760,
    height=900,
    nodes=[
        Node("by-human", 190, 84, 200, "クライアントが依頼", "algr new", HUMAN),
        Node("by-agent", 410, 84, 200, "エージェントが依頼", "CI の失敗・派生タスク", AGENT),
        Node("intake", 300, 200, 200, "intake", "起票エージェント", holder=AGENT),
        Node("needs-decision", 650, 330, 200, "needs-decision", "クライアントが答える", holder=HUMAN),
        Node("ready", 300, 330, 200, "ready", "配車待ち（依存先を待つ）"),
        Node("in-progress", 300, 460, 200, "in-progress", "実装エージェント", holder=AGENT),
        Node("review", 300, 590, 200, "review", "レビューエージェント", holder=AGENT),
        Node("approved", 560, 720, 180, "approved", "メタプログラマーがマージ", holder=HUMAN),
        Node("done", 300, 830, 200, "done", "マージ済み → 蒸留へ（図 3）"),
        Node("failed", 100, 720, 176, "failed", "メタプログラマーが reopen", holder=HUMAN),
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
    legend=["holder-agent", "holder-human", "event"],
)


# --- 図 3：メタの層の中身（判断がルールになり、また判断に戻る輪） ---------------------------

def _on_ring(deg: float, n: Node, cx: float, cy: float, rx: float, ry: float) -> Node:
    a = math.radians(deg)
    n.x, n.y = round(cx + rx * math.cos(a)), round(cy + ry * math.sin(a))
    return n


def _ring_edge(a: Node, b: Node, rx: float, ry: float, kind: str = FLOW) -> Edge:
    """a から b へ、輪に沿った弧で箱の縁から縁まで結ぶ。"""

    def rim(n: Node, tx: float, ty: float) -> tuple[float, float]:
        dx, dy = tx - n.x, ty - n.y
        k = min(n.w / 2 / max(abs(dx), 1e-6), n.h / 2 / max(abs(dy), 1e-6)) * 1.08
        return n.x + dx * k, n.y + dy * k

    (sx, sy), (ex, ey) = rim(a, b.x, b.y), rim(b, a.x, a.y)
    return Edge(f"M{sx:.0f} {sy:.0f}A{rx:g} {ry:g} 0 0 1 {ex:.0f} {ey:.0f}", kind)


_RING = (380, 290, 270, 175)  # 中心と半径
_LOOP = [
    _on_ring(-90, Node("decisions", 0, 0, 220, "判断", "人の答え・方針も、AI の判断も", DATA, 60), *_RING),
    _on_ring(-25, Node("distill", 0, 0, 160, "蒸留エージェント", "人の答えと report を読む", AGENT), *_RING),
    _on_ring(35, Node("proposals", 0, 0, 140, "提案", "足す・外す", DATA, 60), *_RING),
    _on_ring(90, Node("meta", 0, 0, 170, "メタプログラマー", "accept / decline", HUMAN), *_RING),
    _on_ring(145, Node("rules", 0, 0, 150, "ルール", "rules.json（PR で入る）", DATA, 60), *_RING),
    _on_ring(205, Node("agents", 0, 0, 170, "起票・実装・レビュー", "図 2 の処理の層", AGENT), *_RING),
]

META = Diagram(
    id="meta",
    title="メタの層：判断から出て判断に戻る 1 周で、ルールが育つ",
    width=760,
    height=560,
    nodes=[
        *_LOOP,
        Node("report", 380, 260, 180, "k2down report", "落とす／書き直す候補", PLAIN),
        Node("people", 130, 70, 220, "クライアント・メタプログラマー", "", HUMAN, 40),
    ],
    edges=[
        *(
            _ring_edge(_LOOP[i], _LOOP[(i + 1) % 6], _RING[2], _RING[3], READ if i == 4 else WRITE if i == 5 else FLOW)
            for i in range(6)
        ),
        Edge("M380 145V232"),  # 判断 → report
        Edge("M470 260H600V242"),  # report → 蒸留
        Edge("M240 70H300V85", WRITE),  # 人 → 判断（answer / distill）
    ],
    texts=[
        Text(560, 152, "読む", anchor="middle"),
        Text(680, 318, "propose", anchor="middle"),
        Text(560, 468, "algr proposals", anchor="middle"),
        Text(205, 468, "algr accept", anchor="middle"),
        Text(84, 318, "入力", anchor="middle"),
        Text(196, 152, "判断を書く", anchor="middle"),
        Text(388, 200, "集計"),
        Text(250, 54, "answer / distill"),
        Text(380, 340, "ルールを育てるループ", "b", "middle"),
        Text(380, 358, "判断 → 提案 → ルール → 判断", anchor="middle"),
        Text(380, 376, "（decline した提案も判断に残る）", anchor="middle"),
    ],
    legend=[HUMAN, AGENT, DATA, WRITE, READ],
)

DIAGRAMS = (LAYERS, FLOW_DIAGRAM, META)


def svgs() -> dict[str, str]:
    return {d.id: render(d) for d in DIAGRAMS}
