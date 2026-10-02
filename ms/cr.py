"""CR -- Context Runtime. 매 요청 **무엇을 LLM 에 보이고 어떻게 말할지**를 정하고 그 결정을 기록한다.

이 일을 지금까지 "Context Policy · Prompt Policy" 라고 불렀지만 정책(무엇을 해도 되나)이 아니다. 런타임의 결정이다.
CR 이 내는 것을 **Context Decision(CD)** 이라 부른다.

    입력   상태 스냅숏(파생 상태만) · 요청(과업 · 질의) · 지금 그래프에 대한 질의 결과
    출력   ContextDecision = 최소 맥락(MinimalContext) + 정규 프롬프트(CanonicalPrompt) + 결정 기록

    plan(state)          상태 -> 계획(맥락 손잡이 · 프롬프트 꼴). 판본 붙음, 순수 함수(replay 가 다시 계산한다)
    decide(...)          계획대로 질의를 돌리고 최소 맥락과 프롬프트를 짓는다. 판(round)마다 부른다

**경계.** MS 안에서 맥락 · 프롬프트를 짓는 길은 여기 하나다. Pipeline · Runtime 은 CD 를 받아 provider 에 넘기고 WALP 로 판정할 뿐이다
(시험이 붙든다). 지금은 에이전트가 이 역할을 MS 코드 안에서 맡고, 나중에 CR 을 독립 계층으로 옮길 때 끊을 자리가 이 파일이다.

결정 기록의 `prefix_hash` 는 provider 캐시의 앞부분(시스템 글)의 지문이다. 캐시는 뜻이 아니라 **바이트의 앞부분 일치**라서,
이 값이 요청마다 바뀌면 캐시가 깨지고 있다는 뜻이다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .context import ContextPolicy
from .policy import BASE_CONTEXT, FixedContext, FixedPrompt
from .prompt import DEFAULT_LAYOUT, TEMPLATE_VERSIONS, PromptPolicy
from .query import StateQuery, run_query, tool_query

VERSION = "cr-1"


def prefix_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@dataclass
class ContextDecision:
    ctx: object                 # MinimalContext -- WALP 가 "LLM 이 본 것" 으로 쓴다
    prompt: object              # CanonicalPrompt -- provider 어댑터로 간다
    record: dict = field(default_factory=dict)


class ContextRuntime:
    version = VERSION

    def __init__(self, registry, policy: "ContextPolicy | None" = None, prompt_plan: "dict | None" = None,
                 layout: "str | None" = None, retrieve_max: int = 20):
        self.reg = registry
        self.policy = policy or ContextPolicy()
        self.prompt_plan = prompt_plan
        self.layout = layout or DEFAULT_LAYOUT
        self.prompt_policy = PromptPolicy(self.layout)
        self.retrieve_max = retrieve_max

    # -- 상태 -> 계획 ---------------------------------------------------------------------------------------------
    @staticmethod
    def plan(state: dict, context_selector=None, prompt_selector=None, base_context: "dict | None" = None,
             layout: "str | None" = None) -> dict:
        layout = layout or DEFAULT_LAYOUT
        cplan = (context_selector or FixedContext()).plan(state, dict(BASE_CONTEXT, **(base_context or {})))
        pplan = dict((prompt_selector or FixedPrompt()).plan(state), template=TEMPLATE_VERSIONS[layout], layout=layout)
        return {"cr": VERSION, "context_policy": cplan, "prompt_policy": pplan}

    @classmethod
    def from_plan(cls, registry, plan: dict, retrieve_max: int = 20) -> "ContextRuntime":
        c, p = plan["context_policy"], plan["prompt_policy"]
        return cls(registry, ContextPolicy(**c["params"], version=c["version"]), p["plan"], p.get("layout"),
                   retrieve_max)

    # -- 계획 -> 결정 ---------------------------------------------------------------------------------------------
    def minimal_context(self, manager, task, queries, retrieved_ids=(), denied=()):
        qs = [q if isinstance(q, StateQuery) else StateQuery.from_dict(q) for q in queries]
        results = [run_query(q, manager) for q in qs]
        retrieved = []
        if retrieved_ids:
            rq = run_query(StateQuery("retrieved", ids=list(retrieved_ids), limit=self.retrieve_max), manager)
            retrieved = rq.rows
            results = results + [rq]          # 청한 행에도 도구를 고를 수 있게
        offers = tool_query(self.reg, results, manager)
        return self.policy.build(task, [r for r in results if r.name != "retrieved"], offers, retrieved, denied)

    def decide(self, manager, task, queries, retrieved_ids=(), denied=(), preamble: str = "") -> ContextDecision:
        ctx = self.minimal_context(manager, task, queries, retrieved_ids, denied)
        prompt = self.prompt_policy.build(ctx, self.prompt_plan, preamble)
        record = {"cr": self.version, "context_version": self.policy.version, "layout": self.layout,
                  "template": self.prompt_policy.version, "prefix_hash": prefix_hash(prompt.system_text()),
                  "stats": ctx.stats(), "notes": list(prompt.notes)}
        return ContextDecision(ctx, prompt, record)
