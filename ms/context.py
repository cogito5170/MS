"""Context Policy -- 질의 결과를 여섯 동작으로 나눠 최소 맥락을 짓는다.

결정론적이다. LLM 이 고르지 않는다. 어떤 동작을 켤지는 정책 선택기(`policy.py`)가 **상태**를 보고 정한다.

    KEEP       행을 그대로 싣는다. 질의의 `must` 에 맞는 행 · 직전에 RETRIEVE 로 청한 행은 예산을 넘어도 싣는다
               (넘으면 `over_budget` 을 세운다 -- 조용히 자르지 않는다)
    COMPRESS   KEEP 과 같은 행을 표 꼴(열 머리 한 번 · 소수 한 자리)로 싣는다. 본 것으로 친다(`compress=True`)
    SUMMARIZE  예산에 못 실은 행이 `summarize_min` 개 이상이면 결정론적 집계(개수 · 수는 최소/최대/평균 · 나머지는 값 분포)
    RETRIEVE   못 실은 행은 손잡이(handle)로만 남긴다. LLM 이 `retrieve` 도구로 청하면 중재자를 지나 다음 판에 KEEP 으로 온다
    DEFER      질의 하나를 통째로 미룬다(`defer=[질의 이름]`). 행도 요약도 안 싣고 손잡이만 -- RETRIEVE 와 같은 길로 꺼낸다.
               must 행이 하나라도 있는 질의는 미루지 않는다
    DROP       질의의 `droppable` 에 맞는 행을 뺀다(`drop=True`). 손잡이도 없다 -- 대신 `dropped` 에 질의별 개수를 적어
               LLM 이 "빠진 것이 있다" 는 것은 안다. must 행 · 청한 행은 빼지 않는다

입력은 `QueryResult` 뿐이다 -- 그래프를 받지 않는다(원칙 3). 그래서 질의 밖 개체는 맥락에 올 길이 없다.
도구 제안도 KEEP 된 개체로 좁힌다: LLM 은 자기가 본 개체에만 도구를 제안받는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import predicate

KEEP, SUMMARIZE, RETRIEVE = "KEEP", "SUMMARIZE", "RETRIEVE"
DROP, DEFER, COMPRESS = "DROP", "DEFER", "COMPRESS"
ACTIONS = (KEEP, SUMMARIZE, RETRIEVE, DROP, DEFER, COMPRESS)
SEEN = (KEEP, COMPRESS)                     # 중재자가 '본 것' 으로 치는 동작
RISKS = ("read", "local", "external", "irreversible")


def _round(v):
    return round(v, 1) if isinstance(v, float) else v


def summarize(rows) -> dict:
    """행 묶음의 결정론적 요약. LLM 을 부르지 않는다."""
    out = {"count": len(rows)}
    models = {}
    for r in rows:
        models[r.model] = models.get(r.model, 0) + 1
    out["models"] = models
    names = []
    for r in rows:
        for k in r.props:
            if k not in names:
                names.append(k)
    props = {}
    for k in names:
        allv = [r.props[k]["value"] for r in rows if k in r.props]
        vals = [v for v in allv if v is not None]           # 쓸 수 없는 값(None)은 요약에 안 넣고 수만 센다
        unknown = len(allv) - len(vals)
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not vals:
            props[k] = {"n": 0}
        elif nums and len(nums) == len(vals):
            props[k] = {"min": min(nums), "max": max(nums), "mean": round(sum(nums) / len(nums), 3), "n": len(nums)}
        else:
            dist = {}
            for v in vals:
                key = json.dumps(v, ensure_ascii=False)
                dist[key] = dist.get(key, 0) + 1
            top = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            props[k] = {"values": {json.loads(a): b for a, b in top}, "n": len(vals)}
            if len(dist) > 5:
                props[k]["other"] = len(dist) - 5
        if unknown:
            props[k]["unknown"] = unknown
    out["props"] = props
    stale = sum(1 for r in rows if any(v["stale"] for v in r.props.values()))
    if stale:
        out["stale_rows"] = stale
    return out


@dataclass
class MinimalContext:
    task: str
    kept: list = field(default_factory=list)          # [(질의 이름, Row)]
    summaries: list = field(default_factory=list)     # [{"query", "handle", "summary"}]
    handles: dict = field(default_factory=dict)       # handle -> {"query", "ids", "count"}
    offers: list = field(default_factory=list)        # [{"tool", "targets", "card"}]
    denied: list = field(default_factory=list)        # 직전 판에 막힌 제안과 까닭
    seen: dict = field(default_factory=dict)          # KEEP 된 개체 -> 그때의 판(version)
    decisions: dict = field(default_factory=dict)     # 개체 -> ACTIONS 중 하나
    dropped: dict = field(default_factory=dict)       # 질의 -> 뺀 행 수
    compress: bool = False
    matched: int = 0
    budget: int = 0
    used: int = 0
    over_budget: bool = False
    policy: dict = field(default_factory=dict)        # 이 맥락을 지은 정책의 판본 · 손잡이(재현용)

    def _state(self) -> list:
        if not self.compress:
            return [dict(r.payload(), _q=q) for q, r in self.kept]
        tables = {}
        for q, r in self.kept:
            p = r.payload()
            t = tables.setdefault(q, {"_q": q, "cols": [], "rows": []})
            for k in p:
                if k not in t["cols"]:
                    t["cols"].append(k)
            t["rows"].append(p)
        for t in tables.values():
            t["rows"] = [[_round(p.get(c)) for c in t["cols"]] for p in t["rows"]]
        return list(tables.values())

    def restrict_tools(self, risks) -> "MinimalContext":
        """도구 제안을 **좁힌다**(넓히지 않는다). Prompt Policy 의 tool_permission 이 쓴다. 중재자는 좁힌 것을 본다."""
        allow = set(risks)
        self.offers = [o for o in self.offers if o["card"].get("risk") in allow]
        return self

    def payload(self) -> dict:
        d = {"task": self.task,
             "state": self._state(),
             "summaries": self.summaries,
             "handles": {h: {"query": v["query"], "count": v["count"],
                             **({"deferred": True} if v.get("deferred") else {})} for h, v in self.handles.items()},
             "tools": [dict(o["card"], targets=o["targets"]) for o in self.offers]}
        if self.dropped:
            d["dropped"] = self.dropped
        if self.handles:
            d["tools"].append({"name": "retrieve", "risk": "read", "targets": sorted(self.handles),
                               "description": "줄여 둔 행을 다음 판에 받는다. target 에 handle 을 적는다",
                               "params": {}})
        if self.denied:
            d["denied"] = self.denied
        return d

    def render(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, separators=(",", ":"))

    def stats(self) -> dict:
        n = {a: 0 for a in ACTIONS}
        for v in self.decisions.values():
            n[v] += 1
        return {"matched": self.matched, "keep": n[KEEP], "summarize": n[SUMMARIZE], "retrieve_only": n[RETRIEVE],
                "compress": n[COMPRESS], "defer": n[DEFER], "drop": n[DROP],
                "chars": len(self.render()), "budget": self.budget, "over_budget": self.over_budget}


class ContextPolicy:
    def __init__(self, budget_chars: int = 3000, summarize_min: int = 3, keep_max: int = 20, compress: bool = False,
                 defer=(), drop: bool = False, defer_priority_min: "int | None" = None, version: str = "ctx-1"):
        self.budget, self.summarize_min, self.keep_max = budget_chars, summarize_min, keep_max
        self.compress, self.defer, self.drop, self.version = compress, tuple(defer), drop, version
        self.defer_priority_min = defer_priority_min

    def params(self) -> dict:
        return {"budget_chars": self.budget, "summarize_min": self.summarize_min, "keep_max": self.keep_max,
                "compress": self.compress, "defer": list(self.defer), "drop": self.drop,
                "defer_priority_min": self.defer_priority_min}

    def build(self, task: str, results, offers=(), retrieved=(), denied=()) -> MinimalContext:
        """예산은 **실제로 그려지는 글자 수**(`render()`)에 건다. 손으로 센 추정이 아니다."""
        order = sorted(range(len(results)), key=lambda i: (results[i].priority, i))
        cand, seen_ids, dropped, deferred = [], set(), {}, {}
        for row in retrieved:                                   # 직접 청한 것이 먼저
            if row.id not in seen_ids:
                seen_ids.add(row.id)
                cand.append(("retrieved", row, True))
        for i in order:
            r = results[i]
            wants = r.name in self.defer or (self.defer_priority_min is not None and r.priority >= self.defer_priority_min)
            defer_q = wants and not any(row.must for row in r.rows)
            for row in r.rows:
                if row.id in seen_ids:
                    continue
                seen_ids.add(row.id)
                if defer_q:
                    deferred.setdefault(r.name, []).append(row)
                elif self.drop and not row.must and r.droppable and any(
                        predicate.holds(p, row.values()) for p in r.droppable):
                    dropped.setdefault(r.name, []).append(row)
                else:
                    cand.append((r.name, row, row.must))
        matched = sum(r.matched for r in results)
        seen_tag = COMPRESS if self.compress else KEEP

        def assemble(keep: set, summarized: set) -> MinimalContext:
            ctx = MinimalContext(task, budget=self.budget, denied=list(denied), matched=matched,
                                 compress=self.compress, policy={"version": self.version, "params": self.params()})
            groups = {}
            for q, row, _ in cand:
                if id(row) in keep:
                    ctx.kept.append((q, row))
                    ctx.seen[row.id] = row.version
                    ctx.decisions[row.id] = seen_tag
                else:
                    groups.setdefault(q, []).append(row)
            for q, rows in dropped.items():
                ctx.dropped[q] = len(rows)
                for r in rows:
                    ctx.decisions[r.id] = DROP
            for q, rows in deferred.items():                    # 미룬 질의: 손잡이만
                h = f"h{len(ctx.handles) + 1}"
                ctx.handles[h] = {"query": q, "ids": [r.id for r in rows], "count": len(rows), "deferred": True}
                for r in rows:
                    ctx.decisions[r.id] = DEFER
            for q, rows in groups.items():                      # 못 실은 것은 늘 손잡이로 남는다 -- 조용히 사라지지 않는다
                h = f"h{len(ctx.handles) + 1}"
                ctx.handles[h] = {"query": q, "ids": [r.id for r in rows], "count": len(rows)}
                summed = q in summarized and len(rows) >= self.summarize_min
                if summed:
                    ctx.summaries.append({"query": q, "handle": h, "summary": summarize(rows)})
                for r in rows:
                    ctx.decisions[r.id] = SUMMARIZE if summed else RETRIEVE
            for o in offers:                                    # 본 개체에만 도구를
                t = [x for x in o.targets if x in ctx.seen]
                if t:
                    ctx.offers.append({"tool": o.tool, "targets": t, "card": o.card})
            ctx.used = len(ctx.render())
            ctx.over_budget = ctx.used > self.budget
            return ctx

        keep = {id(row) for _, row, must in cand if must}      # 1. must · 청한 것은 예산을 넘어도
        per = {}
        for q, row, must in cand:
            if must:
                per[q] = per.get(q, 0) + 1
        everything = {q for q, _, _ in cand}
        for q, row, must in cand:                               # 2. 예산 안에서 KEEP -- 남는 묶음의 요약 자리를 먼저 떼어 두고
            if must or per.get(q, 0) >= self.keep_max:
                continue
            if assemble(keep | {id(row)}, everything).used <= self.budget:
                keep.add(id(row))
                per[q] = per.get(q, 0) + 1
        ctx = assemble(keep, set())
        summarized = set()
        for h, v in ctx.handles.items():                        # 3. 남은 묶음을 요약 -- 예산이 되면
            if v["count"] >= self.summarize_min and assemble(keep, summarized | {v["query"]}).used <= self.budget:
                summarized.add(v["query"])
        return assemble(keep, summarized)
