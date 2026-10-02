"""한 바퀴 -- 질의 -> 맥락 정책 -> 프롬프트 정책 -> provider -> 응답 -> 제안 -> WALP -> 도구 -> (결과는 텔레메트리로) -> State Manager.

판(round)마다:

    1-3. CR(Context Runtime)이 결정(CD)을 짓는다: STATE QUERY · TOOL QUERY 를 **지금 그래프에** 돌리고, 최소 맥락
         (직전에 retrieve 로 청한 행 · 직전에 막힌 까닭을 함께)과 CanonicalPrompt(도구는 좁히기만)를 낸다
    4. PROVIDER ADAPTER 가 provider 의 요청으로 바꿔 부르고, 응답을 CanonicalResponse 로 정규화한다
    5. 응답은 **제안(Proposal)** 이 된다 -- 함수 호출 응답이어도 실행이 아니다
    6. WALP ARBITER 가 판정한다
         ALLOW retrieve -> 그 handle 의 행을 다음 판에 KEEP 으로 싣고 계속
         ALLOW 도구     -> 실행. 돌려준 것을 텔레메트리로 ingest 하고 끝
         DENY           -> 까닭을 다음 판 맥락의 `denied` 에 싣고 계속(`retry_on_deny`)
         NOOP           -> 끝
    7. `max_rounds` 를 다 쓰면 끝

도구는 6 의 ALLOW 가지 **안에서만** 불린다. 다른 길이 없다(시험이 붙든다).
도구 실행이 터지면 그것도 텔레메트리(`signal="tool_error"`)로 들어간다 -- 모형이 그 신호를 모르면 격리함에 남는다.

`llm` 은 LLMProvider 이거나 예전처럼 `글 -> 글` 함수다(함수면 CallableProvider 로 감싼다). 고정 프롬프트 계획에서 함수가 받는 글은
예전 `build_prompt(ctx)` 와 바이트까지 같다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .arbiter import DENY, NOOP, WalpArbiter
from .canonical import CanonicalRequest
from .context import ContextPolicy
from .cr import ContextRuntime
from .llm import proposal_from
from .prompt import plan_inference
from .providers import CallableProvider, LLMProvider
from .telemetry import Telemetry
from .tools import RETRIEVE


@dataclass
class RunResult:
    rounds: list = field(default_factory=list)
    outcome: str = ""            # executed · noop · denied · exhausted · llm_error
    ingested: list = field(default_factory=list)
    calls: list = field(default_factory=list)       # LLM 호출마다 canonical 사용량 · 지연 · 맥락 크기
    executed: list = field(default_factory=list)    # 실행된 도구 [{"tool", "target"}]

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "rounds": self.rounds, "ingested": self.ingested, "calls": self.calls,
                "executed": self.executed}


class Pipeline:
    """CR(Context Runtime)이 지은 결정(CD)을 provider 에 넘기고, 제안을 WALP 로 판정하고, ALLOW 면 도구를 부른다.
    맥락 · 프롬프트를 여기서 짓지 않는다 -- `self.cr.decide()` 만 부른다."""

    def __init__(self, manager, registry, llm, policy: "ContextPolicy | None" = None,
                 arbiter: "WalpArbiter | None" = None, retrieve_max: int = 20, prompt_plan: "dict | None" = None,
                 model: "str | None" = None, stream: bool = False, tool_mode: str = "text",
                 prompt_layout: "str | None" = None, preamble: str = "", cr: "ContextRuntime | None" = None):
        self.m, self.reg = manager, registry
        self.provider = llm if isinstance(llm, LLMProvider) else CallableProvider(llm)
        self.llm = llm
        self.cr = cr or ContextRuntime(registry, policy, prompt_plan, prompt_layout, retrieve_max)
        self.policy, self.prompt_plan, self.retrieve_max = self.cr.policy, self.cr.prompt_plan, self.cr.retrieve_max
        self.arbiter = arbiter or WalpArbiter(registry, clock=manager.clock)
        self.preamble = preamble
        self.model, self.stream, self.tool_mode = model, stream, tool_mode

    def context(self, task, queries, retrieved_ids=(), denied=()):
        """최소 맥락만(진단 · `ms context` 용). 판마다의 결정은 run() 이 cr.decide() 로 짓는다."""
        return self.cr.minimal_context(self.m, task, queries, retrieved_ids, denied)

    def _call(self, cd):
        prompt = cd.prompt
        req = CanonicalRequest(self.model or self.provider.model, prompt, plan_inference(self.prompt_plan or {}),
                               self.tool_mode, self.stream and self.provider.supports_stream)
        resp = self.provider.collect(req) if req.stream else self.provider.generate(req)
        full = prompt.text()
        ctx_text = prompt.context_text()
        retrieved = [r.payload() for q, r in cd.ctx.kept if q == "retrieved"]
        info = {"provider": resp.provider, "model": resp.model, "finish": resp.finish,
                "usage": resp.to_dict()["usage"], "ttft_ms": resp.ttft_ms, "inference_ms": resp.inference_ms,
                "cost_usd": resp.cost_usd, "unsupported": resp.unsupported + prompt.notes,
                "extensions": resp.extensions, "cd": cd.record,
                "prompt_chars": len(full), "context_chars": len(ctx_text),
                "retrieved_chars": len(json.dumps(retrieved, ensure_ascii=False, separators=(",", ":"))) if retrieved
                else 0, "native_tool_calls": len(resp.tool_calls)}
        return resp, info

    def run(self, task: str, queries, max_rounds: int = 4, retry_on_deny: bool = True) -> RunResult:
        res = RunResult()
        retrieved, denied = [], []
        for i in range(max_rounds):
            cd = self.cr.decide(self.m, task, queries, retrieved, denied, self.preamble)
            ctx = cd.ctx
            rnd = {"round": i + 1}
            res.rounds.append(rnd)
            try:
                resp, info = self._call(cd)
            except Exception as e:
                rnd["context"] = ctx.stats()
                rnd["llm_error"] = f"{type(e).__name__}: {e}"
                res.outcome = "llm_error"
                return res
            rnd["context"], rnd["prompt_chars"] = ctx.stats(), info["prompt_chars"]
            res.calls.append(info)
            p = proposal_from(resp)
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
            tool = self.reg.get(p.tool)                 # 여기 -- ALLOW 가지 안 -- 가 도구가 불리는 유일한 자리
            try:
                obs = tool.run(p.target, p.args)
            except Exception as e:
                obs = [{"entity": p.target, "signal": "tool_error", "value": f"{type(e).__name__}: {e}"}]
            res.executed.append({"tool": tool.name, "target": p.target})
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
