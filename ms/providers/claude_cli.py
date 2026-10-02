"""Claude Code CLI (`claude -p --output-format json`) -- 키 없이 이 컴퓨터의 로그인으로 Claude 를 부른다.

**API 의 Claude 와 같지 않다.** `--system-prompt` 로 기본 시스템 프롬프트를 갈아 끼우고 `--tools ""` 로 도구를 다 꺼도
하네스가 붙이는 몫이 남는다(2026-10-01 실측: 한 줄 요청에 입력 1,100 토큰 남짓). 그래서 평가에서는 B · D · F 칸이 아니라
따로 적는다(사전등록). 대신 이 CLI 는 **비용(total_cost_usd)과 API 시간(duration_api_ms)을 보고한다** -- API 에는 없는 것이다.

    reasoning     -> --effort (low · medium · high). off 는 unsupported
    output_schema -> 프롬프트로만(--json-schema 의 출력 칸을 확인하지 않았다) -- unsupported 로 적는다
    usage         -> Claude 와 같은 모양(claude.usage_from 으로 정규화)
    stream        -> 안 한다(stream-json 을 확인하지 않았다)
"""
from __future__ import annotations

import json
import subprocess
import time

from ..canonical import CanonicalResponse
from .base import LLMProvider, ProviderError
from .claude import finish_of, usage_from


class ClaudeCLIProvider(LLMProvider):
    name = "claude-cli"
    supports_stream = False

    def __init__(self, model: "str | None" = None, argv=("claude",), timeout: float = 300, runner=None,
                 clock=time.perf_counter):
        super().__init__(model or "", api_key="-", transport=object(), clock=clock)
        self.argv, self.timeout = list(argv), timeout
        self.runner = runner or self._run

    def capabilities(self) -> dict:
        return {"provider": self.name, "model": self.model or "(CLI default)", "api": "claude -p (Claude Code harness)",
                "output_schema": "prompt only", "reasoning": "--effort low/medium/high; 'off' unsupported",
                "cached_tokens": "reported (Claude usage shape)", "ttft": "not available (no stream)",
                "stream": False, "native_tools": False, "cost": "reported (total_cost_usd)",
                "prefix_cache": "whole-prompt only -- the CLI places the breakpoint; same system + different user "
                                "text reads 0 (measured 2026-10-02)",
                "not_equivalent_to_api": "Claude Code harness adds system/context tokens"}

    def to_provider_request(self, req):
        p, inf, unsupported = req.prompt, req.inference or {}, []
        system = p.system_text()
        if p.output_schema is not None:
            system += "\nJSON schema: " + json.dumps(p.output_schema, ensure_ascii=False)
            unsupported.append("output_schema=native")
        argv = self.argv + ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                            "--system-prompt", system]
        if req.model or self.model:
            argv += ["--model", req.model or self.model]
        r = inf.get("reasoning")
        if r:
            if r == "off":
                unsupported.append("reasoning=off")
            else:
                argv += ["--effort", r]
        if inf.get("max_output_tokens"):
            unsupported.append("max_output_tokens")
        if req.tool_mode == "native":
            unsupported.append("tool_mode=native")
        return argv, {}, p.user_text(), unsupported

    def _run(self, argv, stdin):
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=self.timeout)
        if p.returncode != 0 and not p.stdout.strip():
            raise ProviderError(f"claude -p 종료 {p.returncode}: {p.stderr.strip()[:300]}")
        return json.loads(p.stdout)

    def generate(self, req):
        argv, _, stdin, unsupported = self.to_provider_request(req)
        t0 = self.clock()
        raw = self.runner(argv, stdin)
        out = self.normalize_response(raw)
        if out.inference_ms is None:
            out.inference_ms = (self.clock() - t0) * 1000
        out.unsupported = unsupported + out.unsupported
        return out

    def stream(self, req):
        raise ProviderError("claude-cli: 스트리밍을 하지 않는다")

    def normalize_usage(self, raw):
        return usage_from((raw or {}).get("usage"))

    def normalize_response(self, raw) -> CanonicalResponse:
        raw = raw or {}
        models = list((raw.get("modelUsage") or {}).keys())
        u = raw.get("usage") or {}
        ext = {"duration_ms": raw.get("duration_ms"), "num_turns": raw.get("num_turns"),
               "stop_reason": raw.get("stop_reason"), "terminal_reason": raw.get("terminal_reason"),
               "cache_creation_input_tokens": u.get("cache_creation_input_tokens"), "models": models}
        err = str(raw.get("result", ""))[:300] if raw.get("is_error") else ""
        return CanonicalResponse(self.name, models[0] if models else (self.model or "?"), str(raw.get("result", "")),
                                 [], "error" if err else finish_of(raw.get("stop_reason") or "end_turn"),
                                 self.normalize_usage(raw), inference_ms=raw.get("duration_api_ms"),
                                 cost_usd=raw.get("total_cost_usd"), extensions={self.name: ext}, error=err)
