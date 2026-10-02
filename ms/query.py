"""State Query · Tool Query -- 그래프에서 LLM 쪽으로 나가는 유일한 길 (원칙 3).

StateQuery (JSON 으로도 적는다):

    {"name": "hot",                        # 맥락에서 이 이름으로 묶인다
     "model": "Server",                    # 생략하면 모든 모형
     "where": [["status", "in", ["hot", "critical"]]],
     "related": {"rel": "contains", "dir": "in", "of": "rack1"},   # rack1 에 담긴 것만 (dir=out 이면 rack1 이 가리키는 것)
     "ids": ["srv1"],                      # 생략 가능
     "select": ["temp_c", "status"],       # 생략하면 전부. 행에 실리는 속성은 이것뿐이다
     "order_by": ["temp_c", "desc"], "limit": 50,
     "priority": 0,                        # 작을수록 먼저 KEEP
     "must": [["status", "==", "critical"]],   # 맞는 행은 예산을 넘어도 KEEP
     "droppable": [["status", "==", "normal"]]}   # 맥락 정책이 drop 을 켜면 뺄 수 있는 행(must 가 이긴다)

행에는 고른 속성의 값 · 나이 · 낡음 여부와, **결과 안의 개체끼리의** 관계만 실린다. 결과 밖 개체의 id 는 안 실린다.

ToolQuery 는 결과의 개체마다 어떤 도구를 **지금** 쓸 수 있는지(대상 모형 · 사전조건 · 신선도)를 고른다.
못 쓰는 도구는 맥락에 안 나간다 -- LLM 은 고를 수 있는 것만 본다. 그래도 중재자는 실행 직전에 다시 본다.
"""
from __future__ import annotations

import json

from dataclasses import dataclass, field

from . import predicate
from .tools import RETRIEVE


@dataclass
class StateQuery:
    name: str
    model: "str | None" = None
    where: list = field(default_factory=list)
    related: "dict | None" = None
    ids: "list | None" = None
    select: "list | None" = None
    order_by: "list | None" = None
    limit: int = 100
    priority: int = 0
    must: list = field(default_factory=list)
    droppable: list = field(default_factory=list)
    allow_stale: bool = False   # 낡은 속성 값도 LLM 에 보인다(_stale 표시와 함께). 아니면 값은 None 이고 _unusable (BD-65)

    @classmethod
    def from_dict(cls, d: dict) -> "StateQuery":
        q = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        for p in list(q.where) + list(q.must) + list(q.droppable):
            bad = predicate.check(p)
            if bad:
                raise ValueError(f"질의 {q.name}: {bad[0]}")
        return q


@dataclass
class Row:
    id: str
    model: str
    version: int
    props: dict             # 이름 -> {"value", "age", "stale"[, "usable"]}  usable 이 False 면 값을 쓸 수 없다(값 None)
    must: bool = False
    edges: list = field(default_factory=list)   # [관계, 출발, 도착] -- 양끝이 다 결과 안에 있는 것만

    def values(self) -> dict:
        return {k: v["value"] for k, v in self.props.items()}

    def payload(self) -> dict:
        out = {"id": self.id, "model": self.model}
        for k, v in self.props.items():
            out[k] = v["value"]
            if not v.get("usable", True):
                out.setdefault("_unusable", []).append(k)       # 값을 모른다(낡음 · 모름) -- 지어내지 않는다
            elif v["stale"]:
                out.setdefault("_stale", []).append(k)          # 질의가 allow_stale 을 명시해서 낡은 값을 보인다
        if self.edges:
            out["_edges"] = self.edges
        return out


@dataclass
class QueryResult:
    name: str
    rows: list
    matched: int            # limit 전 개수
    priority: int = 0
    droppable: list = field(default_factory=list)


def run_query(q: StateQuery, manager) -> QueryResult:
    g = manager.graph
    cand = list(g.nodes.values())
    if q.model:
        cand = [n for n in cand if n.model == q.model]
    if q.ids is not None:
        want = set(q.ids)
        cand = [n for n in cand if n.id in want]
    if q.related:
        of, rel, d = q.related["of"], q.related.get("rel"), q.related.get("dir", "out")
        if of not in g.nodes:
            near = set()
        else:
            near = set(g.out(of, rel) if d == "out" else g.inn(of, rel))
        cand = [n for n in cand if n.id in near]
    cand = [n for n in cand if predicate.all_hold(q.where, n.values())]
    if q.order_by:
        key, desc = q.order_by[0], (len(q.order_by) > 1 and q.order_by[1] == "desc")
        have = [n for n in cand if n.props.get(key) is not None]
        miss = [n for n in cand if n.props.get(key) is None]
        try:
            have.sort(key=lambda n: n.props[key].value, reverse=desc)
        except TypeError:
            pass
        cand = have + miss
    else:
        cand.sort(key=lambda n: n.id)
    matched = len(cand)
    cand = cand[: q.limit]
    ids = {n.id for n in cand}
    rows = []
    for n in cand:
        names = q.select if q.select is not None else list(n.props)
        props = {}
        for p in names:
            if p in n.props:
                props[p] = {"value": n.props[p].value, "age": manager.age(n.id, p),
                            "stale": manager.is_stale(n.id, p)}
        edges = sorted([list(e) for e in g.edges if (e[1] == n.id and e[2] in ids)])
        rows.append(Row(n.id, n.model, n.version, props, predicate.all_hold(q.must, n.values()) if q.must else False,
                        edges))
    return QueryResult(q.name, rows, matched, q.priority, list(q.droppable))


@dataclass
class ToolOffer:
    tool: str
    targets: list
    card: dict


def unmet(tool, nid: str, manager) -> list:
    """이 도구를 이 개체에 **지금** 못 쓰는 까닭들(비었으면 쓸 수 있다). 중재자도 이것을 부른다."""
    node = manager.graph.nodes.get(nid)
    if node is None:
        return [f"개체 {nid} 가 없다"]
    if tool.target_model not in ("*", node.model):
        return [f"{tool.name} 는 {tool.target_model} 용인데 {nid} 는 {node.model}"]
    out = []
    for p in predicate.props_of(tool.preconditions):
        if manager.is_stale(nid, p):
            age = manager.age(nid, p)
            out.append(f"{nid}.{p} 가 " + ("없다" if age is None else f"낡았다({age:.0f}s)"))
    if not out:
        vals = node.values()
        for p in tool.preconditions:
            if not predicate.holds(p, vals):
                out.append(f"사전조건 {p} 가 {nid} 에서 거짓 ({p[0]}={vals.get(p[0])!r})")
    return out


def tool_query(registry, results, manager) -> list:
    ids = []
    for r in results:
        for row in r.rows:
            if row.id not in ids:
                ids.append(row.id)
    offers = []
    for t in registry.tools.values():
        if t.name == RETRIEVE:
            continue
        ok = [nid for nid in ids if not unmet(t, nid, manager)]
        if ok:
            offers.append(ToolOffer(t.name, ok, t.card()))
    return offers


def withhold_stale(res: QueryResult, allow_stale: bool) -> QueryResult:
    """BD-65: 낡은 속성 값은 LLM 쪽으로 내지 않는다(값 None · usable False). 질의가 allow_stale 을 명시했을 때만 값과 _stale 표시를 둔다.
    `run_query` 자체는 그대로다 -- 중재자 · 도구 조건 · DC 의 MSGraphSource 는 원래 값과 stale 표시를 본다."""
    if allow_stale:
        return res
    rows = []
    for r in res.rows:
        props = {k: ({"value": None, "age": v["age"], "stale": True, "usable": False} if v["stale"] else v)
                 for k, v in r.props.items()}
        rows.append(Row(r.id, r.model, r.version, props, r.must, r.edges))
    return QueryResult(res.name, rows, res.matched, res.priority, res.droppable)


USABLE = ("OBSERVED", "DERIVED", "INFERRED")        # DC 의 유효성 가운데 '지금 값' 으로 쓸 수 있는 것


def result_from_dc(name: str, q: dict, manager) -> QueryResult:
    """DC 결정 문맥의 질의 결과(core: rows [{id, model, props {속성: [값 | None, 유효성]}, must, edges}], matched, priority,
    droppable) -> MS QueryResult. 값과 유효성은 DC 가 정한 그대로다(쓸 수 없으면 None). 판(version)은 MS 세계 그래프에서
    읽는다 -- DC 는 판을 싣지 않고, 결정 문맥은 이 요청의 시작에 같은 그래프로 지었다(그 사이에 들어온 관측이 없다)."""
    rows = []
    for r in q["rows"]:
        node = manager.graph.nodes.get(r["id"])
        props = {}
        for p, (v, st) in sorted(r["props"].items()):
            props[p] = {"value": v, "age": None, "stale": st == "STALE", "usable": st in USABLE or (st == "STALE" and v is not None)}
        rows.append(Row(r["id"], r["model"], node.version if node is not None else -1, props, bool(r.get("must")),
                        [list(e) for e in r.get("edges", ())]))
    drop = q.get("droppable", [])
    return QueryResult(name, rows, int(q["matched"]), int(q.get("priority", 0)),
                       json.loads(drop) if isinstance(drop, str) else list(drop))
