"""한 바퀴 -- 질의 -> 맥락 정책 -> LLM -> 제안 -> 중재자 -> 도구 -> (결과는 텔레메트리로) -> State Manager.

판(round)마다:

    1. STATE QUERY 를 **지금 그래프에** 돌리고 TOOL QUERY 로 쓸 수 있는 도구를 고른다
    2. CONTEXT POLICY 가 최소 맥락을 짓는다(직전에 retrieve 로 청한 행 · 직전에 막힌 까닭을 함께)
    3. LLM 이 제안 하나를 낸다
    4. WALP ARBITER 가 판정한다
         ALLOW retrieve -> 그 handle 의 행을 다음 판에 KEEP 으로 싣고 계속
         ALLOW 도구     -> 실행. 돌려준 것을 텔레메트리로 ingest 하고 끝
         DENY           -> 까닭을 다음 판 맥락의 `denied` 에 싣고 계속(`retry_on_deny`)
         NOOP           -> 끝
    5. `max_rounds` 를 다 쓰면 끝

도구 실행이 터지면 그것도 텔레메트리(`signal="tool_error"`)로 들어간다 -- 모형이 그 신호를 모르면 격리함에 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .arbiter import ALLOW, DENY, NOOP, WalpArbiter
from .context import ContextPolicy
from .llm import build_prompt, parse_proposal
from .query import StateQuery, run_query, tool_query
from .telemetry import Telemetry
from .tools import RETRIEVE


@dataclass
class RunResult:
    rounds: list = field(default_factory=list)
    outcome: str = ""            # executed · noop · denied · exhausted · llm_error
    ingested: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "rounds": self.rounds, "ingested": self.ingested}


class Pipeline:
    def __init__(self, manager, registry, llm, policy: "ContextPolicy | None" = None,
                 arbiter: "WalpArbiter | None" = None, retrieve_max: int = 20):
        self.m, self.reg, self.llm = manager, registry, llm
        self.policy = policy or ContextPolicy()
        self.arbiter = arbiter or WalpArbiter(registry, clock=manager.clock)
        self.retrieve_max = retrieve_max

    def context(self, task, queries, retrieved_ids=(), denied=()):
        qs = [q if isinstance(q, StateQuery) else StateQuery.from_dict(q) for q in queries]
        results = [run_query(q, self.m) for q in qs]
        retrieved = []
        if retrieved_ids:
            rq = run_query(StateQuery("retrieved", ids=list(retrieved_ids), limit=self.retrieve_max), self.m)
            retrieved = rq.rows
            results = results + [rq]          # 청한 행에도 도구를 고를 수 있게
        offers = tool_query(self.reg, results, self.m)
        return self.policy.build(task, [r for r in results if r.name != "retrieved"], offers, retrieved, denied)

    def run(self, task: str, queries, max_rounds: int = 4, retry_on_deny: bool = True) -> RunResult:
        res = RunResult()
        retrieved, denied = [], []
        for i in range(max_rounds):
            ctx = self.context(task, queries, retrieved, denied)
            prompt = build_prompt(ctx)
            rnd = {"round": i + 1, "context": ctx.stats(), "prompt_chars": len(prompt)}
            res.rounds.append(rnd)
            try:
                text = self.llm(prompt)
            except Exception as e:
                rnd["llm_error"] = f"{type(e).__name__}: {e}"
                res.outcome = "llm_error"
                return res
            p = parse_proposal(text)
            d = self.arbiter.decide(p, ctx, self.m)
            rnd["proposal"], rnd["decision"] = p.to_dict(), d.to_dict()
            if d.verdict == NOOP:
                res.outcome = "noop"
                return res
            if d.verdict == DENY:
                denied = [{"tool": p.tool, "target": p.target, "rule": d.rule, "reasons": d.reasons}]
                if not retry_on_deny:
                    res.outcome = "denied"
                    return res
                continue
            denied = []
            if p.tool == RETRIEVE:
                for nid in ctx.handles[p.target]["ids"][: self.retrieve_max]:
                    if nid not in retrieved:
                        retrieved.append(nid)
                continue
            tool = self.reg.get(p.tool)
            try:
                obs = tool.run(p.target, p.args)
            except Exception as e:
                obs = [{"entity": p.target, "signal": "tool_error", "value": f"{type(e).__name__}: {e}"}]
            for o in obs:
                o = dict(o)
                o.setdefault("entity", p.target)
                o.setdefault("source", f"tool:{tool.name}")
                o.setdefault("ts", self.m.clock())
                r = self.m.ingest(Telemetry.from_dict(o))
                res.ingested.append({"signal": o["signal"], "status": r.status, "reason": r.reason,
                                     "changes": {k: list(v) for k, v in r.changes.items()}})
            rnd["executed"] = True
            res.outcome = "executed"
            return res
        res.outcome = "denied" if denied else "exhausted"
        return res
