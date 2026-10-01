"""provider 등록부 -- 위 계층은 이름으로만 provider 를 얻는다(`make_provider`). 구체 어댑터 모듈을 import 하지 않는다.

    openai       OpenAIProvider     (OPENAI_API_KEY, 모형 이름 필수)
    claude       ClaudeProvider     (ANTHROPIC_API_KEY, 기본 claude-opus-5-5)
    gemini       GeminiProvider     (GEMINI_API_KEY, 모형 이름 필수)
    claude-cli   ClaudeCLIProvider  (claude -p -- API 와 같지 않다)
    sim-openai · sim-claude · sim-gemini   같은 어댑터 + 모의 transport. **배선 확인 전용**

모형 이름의 기본값을 지어내지 않는다 -- OpenAI · Gemini 는 사용자가 준다.
"""
from __future__ import annotations

import time

from ..canonical import CanonicalResponse
from .base import LLMProvider, ProviderError
from .claude import ClaudeProvider
from .claude_cli import ClaudeCLIProvider
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from .simulated import SimulatedTransport, toy_agent

ADAPTERS = {"openai": OpenAIProvider, "claude": ClaudeProvider, "gemini": GeminiProvider}
DEFAULT_MODEL = {"claude": "claude-opus-5-5"}
NAMES = ("openai", "claude", "gemini", "claude-cli", "sim-openai", "sim-claude", "sim-gemini")


def make_provider(name: str, model: "str | None" = None, **kw) -> LLMProvider:
    if name == "claude-cli":
        return ClaudeCLIProvider(model, **kw)
    if name.startswith("sim-"):
        dialect = name[4:]
        if dialect not in ADAPTERS:
            raise ProviderError(f"모르는 모의 provider {name}")
        p = ADAPTERS[dialect](model or f"sim-{dialect}", api_key="simulated",
                              transport=SimulatedTransport(dialect, kw.pop("responder", toy_agent),
                                                           kw.pop("latency_ms", 0.0)), **kw)
        p.simulated = True
        return p
    if name not in ADAPTERS:
        raise ProviderError(f"모르는 provider {name} (있는 것: {', '.join(NAMES)})")
    model = model or DEFAULT_MODEL.get(name)
    if not model:
        raise ProviderError(f"{name}: 모형 이름을 주라(--model). 기본값을 지어내지 않는다")
    return ADAPTERS[name](model, **kw)


class CallableProvider(LLMProvider):
    """`글 -> 글` 함수(ScriptedLLM · CommandLLM)를 provider 로. 사용량을 모르므로 Usage 는 전부 None 이다."""
    name = "callable"
    supports_stream = False

    def __init__(self, fn, clock=time.perf_counter):
        super().__init__("callable", api_key="-", transport=object(), clock=clock)
        self.fn = fn

    def capabilities(self) -> dict:
        return {"provider": self.name, "output_schema": "prompt only", "reasoning": "unsupported",
                "cached_tokens": "not reported", "ttft": "not available", "stream": False, "native_tools": False,
                "cost": "not reported"}

    def to_provider_request(self, req):
        import json
        text, unsupported = req.prompt.text(), []
        if req.prompt.output_schema is not None:
            text = text.rstrip("\n") + "\nJSON schema: " + json.dumps(req.prompt.output_schema, ensure_ascii=False) + "\n"
            unsupported.append("output_schema=native")
        unsupported += [f"{k}={v}" for k, v in (req.inference or {}).items() if v]
        return None, {}, text, unsupported

    def generate(self, req):
        _, _, text, unsupported = self.to_provider_request(req)
        t0 = self.clock()
        out = self.fn(text)
        return CanonicalResponse(self.name, "callable", out, inference_ms=(self.clock() - t0) * 1000,
                                 unsupported=unsupported)

    def stream(self, req):
        raise ProviderError("callable: 스트리밍을 하지 않는다")


__all__ = ["LLMProvider", "ProviderError", "make_provider", "CallableProvider", "NAMES", "SimulatedTransport", "toy_agent"]
