"""Context Policy -- 질의 결과를 KEEP · SUMMARIZE · RETRIEVE 로 나눠 최소 맥락을 짓는다.

결정론적이다. LLM 이 고르지 않는다.

    KEEP       행을 그대로 싣는다. 질의의 `must` 에 맞는 행 · 직전에 RETRIEVE 로 청한 행은 예산을 넘어도 싣는다
               (넘으면 `over_budget` 을 세운다 -- 조용히 자르지 않는다)
    SUMMARIZE  예산에 못 실은 행이 `summarize_min` 개 이상이면 결정론적 집계(개수 · 수는 최소/최대/평균 · 나머지는 값 분포)
    RETRIEVE   못 실은 행은 손잡이(handle)로만 남긴다. LLM 이 `retrieve` 도구로 청하면 중재자를 지나 다음 판에 KEEP 으로 온다

입력은 `QueryResult` 뿐이다 -- 그래프를 받지 않는다(원칙 3). 그래서 질의 밖 개체는 맥락에 올 길이 없다.
도구 제안도 KEEP 된 개체로 좁힌다: LLM 은 자기가 본 개체에만 도구를 제안받는다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

KEEP, SUMMARIZE, RETRIEVE = "KEEP", "SUMMARIZE", "RETRIEVE"


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
        vals = [r.props[k]["value"] for r in rows if k in r.props]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len(vals):
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
    decisions: dict = field(default_factory=dict)     # 개체 -> KEEP · SUMMARIZE · RETRIEVE
    matched: int = 0
    budget: int = 0
    used: int = 0
    over_budget: bool = False

    def payload(self) -> dict:
        d = {"task": self.task,
             "state": [dict(r.payload(), _q=q) for q, r in self.kept],
             "summaries": self.summaries,
             "handles": {h: {"query": v["query"], "count": v["count"]} for h, v in self.handles.items()},
             "tools": [dict(o["card"], targets=o["targets"]) for o in self.offers]}
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
        n = {KEEP: 0, SUMMARIZE: 0, RETRIEVE: 0}
        for v in self.decisions.values():
            n[v] += 1
        return {"matched": self.matched, "keep": n[KEEP], "summarize": n[SUMMARIZE], "retrieve_only": n[RETRIEVE],
                "chars": len(self.render()), "budget": self.budget, "over_budget": self.over_budget}


class ContextPolicy:
    def __init__(self, budget_chars: int = 3000, summarize_min: int = 3, keep_max: int = 20):
        self.budget, self.summarize_min, self.keep_max = budget_chars, summarize_min, keep_max

    def build(self, task: str, results, offers=(), retrieved=(), denied=()) -> MinimalContext:
        """예산은 **실제로 그려지는 글자 수**(`render()`)에 건다. 손으로 센 추정이 아니다."""
        order = sorted(range(len(results)), key=lambda i: (results[i].priority, i))
        cand, seen_ids = [], set()
        for row in retrieved:                                   # 직접 청한 것이 먼저
            if row.id not in seen_ids:
                seen_ids.add(row.id)
                cand.append(("retrieved", row, True))
        for i in order:
            r = results[i]
            for row in r.rows:
                if row.id not in seen_ids:
                    seen_ids.add(row.id)
                    cand.append((r.name, row, row.must))
        matched = sum(r.matched for r in results)

        def assemble(keep: set, summarized: set) -> MinimalContext:
            ctx = MinimalContext(task, budget=self.budget, denied=list(denied), matched=matched)
            groups = {}
            for q, row, _ in cand:
                if id(row) in keep:
                    ctx.kept.append((q, row))
                    ctx.seen[row.id] = row.version
                    ctx.decisions[row.id] = KEEP
                else:
                    groups.setdefault(q, []).append(row)
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
