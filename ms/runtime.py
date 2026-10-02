"""MS API · Request Manager -- 요청 하나를 받아 정책 루프를 한 번 돈다.

    USER -> handle(request)
      1. 세션의 **상태**를 읽는다(usage_model.snapshot -- 파생 상태만)
      2. Context Policy · Prompt Policy · Provider Policy 가 상태에서 계획을 고른다(판본 붙음)
      3. Pipeline: State Query -> Context Policy -> Prompt Policy -> Provider Adapter -> Proposal -> Arbiter -> Tool
      4. 실행을 **정규 텔레메트리**(RunRecord)로 적는다
      5. 그 텔레메트리를 State Manager 에 넣는다 -> 모형이 다음 상태를 정한다   (적응 루프가 닫힌다)

MS 는 provider 가 아니다. 추론은 provider 가 하고, MS 는 그 위에서 무엇을 보일지 · 어떻게 말할지 · 누구에게 물을지 · 제안을 받을지를
정한다. 연구 대상은 이 고리다: Telemetry -> State -> Context/Prompt Policy -> Provider -> LLM -> Arbiter.

세계의 상태(서버 · 랙)와 사용의 상태(세션)는 같은 State Manager 에 둘 수도, 따로 둘 수도 있다(`usage_manager`). 평가는 과업마다
세계를 새로 짓고 세션 상태는 이어 가야 해서 따로 둔다. 어느 쪽이든 같은 Model · Relationship 구조다.

성공 판정(`success` · `forbidden` · `expect_noop`)을 요청에 주면 실행 뒤 **그래프**를 보고 채운다. LLM 의 말로 판정하지 않는다.
"""
from __future__ import annotations

import itertools
import json
import time

from . import predicate
from . import usage_model as U
from .arbiter import DENY, Arbiter
from .cr import VERSION as CR_VERSION, ContextRuntime
from .pipeline import Pipeline
from .policy import BASE_CONTEXT, ExplicitProvider, FixedContext, FixedPrompt
from .run_telemetry import RunRecord, cost_of
from .tools import RETRIEVE

_ids = itertools.count(1)


def check_success(manager, request: dict, executed: list):
    """과업의 성공 기준을 **최종 상태 그래프**로 본다. 기준이 없으면 None."""
    if not any(k in request for k in ("success", "forbidden", "expect_noop")):
        return None
    tools = [e["tool"] for e in executed]
    if any(t in (request.get("forbidden") or []) for t in tools):
        return False
    if request.get("expect_noop") and tools:
        return False
    for ent, prop, *rest in request.get("success") or []:
        node = manager.graph.nodes.get(ent)
        if node is None or not predicate.holds([prop, *rest], node.values()):
            return False
    return True


class Runtime:
    def __init__(self, manager, registry, providers: dict, *, grants=(), context_selector=None, prompt_selector=None,
                 provider_policy=None, base_context: "dict | None" = None, prices: "dict | None" = None,
                 ledger_path: "str | None" = None, max_rounds: int = 4, wall=time.perf_counter,
                 usage_manager=None, prompt_layout: "str | None" = None):
        self.m, self.reg, self.providers = manager, registry, dict(providers)
        self.um = usage_manager or manager
        from .prompt import DEFAULT_LAYOUT, TEMPLATE_VERSIONS
        self.prompt_layout = prompt_layout or DEFAULT_LAYOUT
        self.template_version = TEMPLATE_VERSIONS[self.prompt_layout]
        U.install(self.um)
        self.arbiter = Arbiter(registry, grants, clock=manager.clock)
        self.ctx_sel = context_selector or FixedContext()
        self.prompt_sel = prompt_selector or FixedPrompt()
        default = next(iter(self.providers)) if self.providers else None
        self.provider_policy = provider_policy or ExplicitProvider(default)
        self.base_context = dict(BASE_CONTEXT, **(base_context or {}))
        self.prices, self.ledger_path, self.max_rounds, self.wall = prices, ledger_path, max_rounds, wall
        self.records: dict = {}

    def open_session(self, name: str, budgets: dict) -> str:
        return U.open_session(self.um, name, budgets)

    def handle(self, request: dict) -> dict:
        t0 = self.wall()
        sid = U.session_id(request["session"])
        if sid not in self.um.graph.nodes:
            raise KeyError(f"세션 {request['session']} 이 열리지 않았다(open_session)")
        state = U.snapshot(self.um, sid)
        plan = ContextRuntime.plan(state, self.ctx_sel, self.prompt_sel, self.base_context, self.prompt_layout)
        cplan, pplan = plan["context_policy"], plan["prompt_policy"]
        choice = self.provider_policy.select(state, request)
        provider = self.providers[choice["provider"]]
        pipe = Pipeline(self.m, self.reg, provider, None, self.arbiter,
                        model=choice.get("model"), stream=bool(request.get("stream")),
                        tool_mode=request.get("tool_mode", "text"), preamble=request.get("preamble", ""),
                        cr=ContextRuntime.from_plan(self.reg, plan))
        res = pipe.run(request["task"], request.get("queries", ()), max_rounds=request.get("max_rounds", self.max_rounds))
        total_ms = (self.wall() - t0) * 1000
        rec = self._record(request, sid, state, cplan, pplan, choice, provider, res, total_ms)
        U.link_provider(self.um, sid, choice["provider"])
        matched = res.rounds[0]["context"]["matched"] if res.rounds and "context" in res.rounds[0] else None
        for sig in rec.to_signals(sid, self.um.clock(), matched_rows=matched):
            self.um.ingest(sig)
        self.records[rec.run["run_id"]] = rec
        self._ledger({"kind": "run", "record": rec.to_dict()})
        return {"run_id": rec.run["run_id"], "record": rec.to_dict(), "result": res.to_dict()}

    def feedback(self, run_id: str, user_correction: bool):
        """사람이 그 실행을 고쳤나. 그것도 텔레메트리다 -- correction_rate 상태가 여기서 나온다."""
        rec = self.records[run_id]
        rec.outcome["user_correction"] = bool(user_correction)
        sid = U.session_id(rec.run["session_id"])
        r = self.um.ingest({"source": "user", "entity": sid, "signal": "outcome.user_correction",
                            "value": bool(user_correction), "ts": self.um.clock(), "meta": {"run_id": run_id}})
        self._ledger({"kind": "feedback", "run_id": run_id, "user_correction": bool(user_correction)})
        return r

    # -- 기록 ---------------------------------------------------------------------------------------------------
    def _record(self, request, sid, state, cplan, pplan, choice, provider, res, total_ms) -> RunRecord:
        calls = res.calls
        decisions = [r["decision"] for r in res.rounds if "decision" in r]
        proposals = [r["proposal"] for r in res.rounds if "proposal" in r]

        def tsum(key):
            vals = [c["usage"][key] for c in calls]
            return None if not vals or any(v is None for v in vals) else sum(vals)

        def share(chars_key):
            vals = []
            for c in calls:
                i = c["usage"]["input_tokens"]
                if i is None or not c["prompt_chars"]:
                    return None
                vals.append(i * c[chars_key] / c["prompt_chars"])
            return round(sum(vals)) if vals else None

        denies = sum(1 for d in decisions if d["verdict"] == DENY)
        invalid = sum(1 for d in decisions if d["rule"] == "A0")
        retrievals = sum(1 for d, p in zip(decisions, proposals) if d["verdict"] == "ALLOW" and p["tool"] == RETRIEVE)
        retries = sum(1 for d in decisions[:-1] if d["verdict"] == DENY) if len(decisions) > 1 else 0
        tool_ok = None
        if res.executed:
            tool_ok = not any(i["signal"] == "tool_error" for i in res.ingested)
        tokens = {"input_tokens": tsum("input_tokens"), "output_tokens": tsum("output_tokens"),
                  "cached_input_tokens": tsum("cached_input_tokens"), "context_tokens": share("context_chars"),
                  "retrieved_tokens": share("retrieved_chars"), "total_tokens": tsum("total_tokens")}
        if tokens["total_tokens"] is None and tokens["input_tokens"] is not None and tokens["output_tokens"] is not None:
            tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
        inf = [c["inference_ms"] for c in calls]
        latency = {"ttft_ms": calls[0]["ttft_ms"] if calls else None,
                   "inference_ms": None if not inf or any(v is None for v in inf) else round(sum(inf), 3),
                   "total_ms": round(total_ms, 3)}
        reported = [c["cost_usd"] for c in calls]
        if calls and all(v is not None for v in reported):
            cost = {"usd": sum(reported), "source": "provider"}
        else:
            c = cost_of(calls[0]["model"] if calls else "", tokens, self.prices)
            cost = {"usd": c, "source": "price_table" if c is not None else None}
        final = decisions[-1] if decisions else None
        run_id = request.get("run_id") or f"run-{next(_ids)}-{int(time.time() * 1000) % 10**8}"
        exts = {}
        for c in calls:
            for k, v in (c["extensions"] or {}).items():
                exts.setdefault(k, []).append(v)
        unsupported = sorted({u for c in calls for u in c["unsupported"]})
        return RunRecord(
            run={"run_id": run_id, "session_id": request["session"], "provider": choice["provider"],
                 "model": calls[0]["model"] if calls else choice.get("model"), "timestamp": self.m.clock(),
                 "simulated": bool(getattr(provider, "simulated", False))},
            tokens=tokens, latency=latency,
            interaction={"llm_calls": len(calls), "tool_calls": len(res.executed), "retries": retries,
                         "context_retrievals": retrievals, "arbiter_denies": denies, "proposal_invalid": invalid,
                         "non_progress_rounds": denies + retrievals},
            outcome={"task_success": check_success(self.m, request, res.executed), "user_correction": None,
                     "tool_success": tool_ok},
            policy={"cr": CR_VERSION, "state": state, "context_policy": cplan, "prompt_policy": pplan,
                    "provider_policy": choice,
                    "arbiter_decision": {"final": final, "all": [[d["verdict"], d["rule"]] for d in decisions]},
                    "inputs": {"base_context": self.base_context, "default_provider": self.provider_policy.default
                               if isinstance(self.provider_policy, ExplicitProvider) else None}},
            cost=cost,
            estimated={"context_tokens": "input_tokens × context_chars/prompt_chars",
                       "retrieved_tokens": "input_tokens × retrieved_chars/prompt_chars"},
            unsupported=unsupported, extensions=exts)

    def _ledger(self, rec: dict):
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
