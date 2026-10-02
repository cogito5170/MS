"""Gemini -- `POST /v1beta/models/{model}:generateContent` (스트리밍: `:streamGenerateContent?alt=sse`).

canonical -> Gemini:
    system_text      -> systemInstruction.parts[0].text
    user_text        -> contents[0] (role user)
    output_schema    -> generationConfig.responseMimeType = application/json **만**. 스키마 강제 필드 이름을
                        이번 조사에서 확인하지 못했다(선행조사 표) -- 그래서 스키마는 프롬프트로만 준다. 흉내 내지 않는다
    reasoning        -> generationConfig.thinkingConfig.thinkingLevel -- Gemini 3 계열에서 low · high 만.
                        medium · off 와 2.5 계열(thinkingBudget 은 토큰 수라 수준을 수로 바꾸는 것은 지어내기다)은 unsupported
    max_output_tokens-> generationConfig.maxOutputTokens
    native tools     -> tools[0].functionDeclarations

Gemini -> canonical usage (OTel 규약):
    promptTokenCount + toolUsePromptTokenCount -> input_tokens (promptTokenCount 는 캐시 포함)
    cachedContentTokenCount                    -> cached_input_tokens
    candidatesTokenCount + thoughtsTokenCount  -> output_tokens
        (Gemini 의 candidatesTokenCount 는 사고를 **뺀** 몫이다. OpenAI · Claude 의 output 은 사고를 포함한다)
    thoughtsTokenCount                         -> extensions.gemini.thoughts_tokens
"""
from __future__ import annotations

import json

from ..canonical import CanonicalResponse, ToolCall, Usage
from .base import LLMProvider, native_tools

BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "refusal", "RECITATION": "refusal",
          "PROHIBITED_CONTENT": "refusal", "BLOCKLIST": "refusal", "SPII": "refusal"}


def _gemini3(model: str) -> bool:
    return model.startswith("gemini-3")


class GeminiProvider(LLMProvider):
    name = "gemini"
    env_key = "GEMINI_API_KEY"

    def capabilities(self) -> dict:
        return {"provider": self.name, "model": self.model, "api": "generateContent",
                "output_schema": "json mime type only; schema given in prompt (schema field unverified)",
                "reasoning": "thinkingLevel low/high on gemini-3*; otherwise unsupported" if _gemini3(self.model)
                else "unsupported for this model (thinkingBudget is a token count, not mapped)",
                "cached_tokens": "reported (cachedContentTokenCount, included in promptTokenCount)",
                "prefix_cache": "implicit by provider if any (not configured here; unverified)",
                "ttft": "stream only", "stream": True, "native_tools": True, "cost": "not reported (price table)"}

    def to_provider_request(self, req):
        p, inf, unsupported = req.prompt, req.inference or {}, []
        model = req.model or self.model
        gc = {}
        if inf.get("max_output_tokens"):
            gc["maxOutputTokens"] = int(inf["max_output_tokens"])
        r = inf.get("reasoning")
        if r:
            if _gemini3(model) and r in ("low", "high"):
                gc["thinkingConfig"] = {"thinkingLevel": r}
            else:
                unsupported.append(f"reasoning={r}")
        system = p.system_text()
        if p.output_schema is not None and req.tool_mode == "text":
            gc["responseMimeType"] = "application/json"
            system += "\nJSON schema: " + json.dumps(p.output_schema, ensure_ascii=False)
            unsupported.append("output_schema=native")
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": p.user_text()}]}]}
        if gc:
            body["generationConfig"] = gc
        if req.tool_mode == "native" and p.tools:
            body["tools"] = [{"functionDeclarations": native_tools(p)}]
        method = "streamGenerateContent?alt=sse" if req.stream else "generateContent"
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        return f"{BASE}{model}:{method}", headers, body, unsupported

    def normalize_usage(self, raw) -> Usage:
        u = (raw or {}).get("usageMetadata") or {}
        p, tp = u.get("promptTokenCount"), u.get("toolUsePromptTokenCount")
        c, th = u.get("candidatesTokenCount"), u.get("thoughtsTokenCount")
        i = None if p is None else p + (tp or 0)
        o = None if c is None and th is None else (c or 0) + (th or 0)
        cached = u.get("cachedContentTokenCount")
        if cached is None and i is not None:
            cached = 0
        return Usage(i, o, cached, u.get("totalTokenCount"))

    def normalize_response(self, raw) -> CanonicalResponse:
        raw = raw or {}
        cands = raw.get("candidates") or []
        text, calls, reason = [], [], None
        if cands:
            reason = cands[0].get("finishReason")
            for part in (cands[0].get("content") or {}).get("parts") or []:
                if "text" in part and not part.get("thought"):
                    text.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    args = fc.get("args")
                    calls.append(ToolCall(fc.get("name", ""), args if isinstance(args, dict) else {"_unparsed": True}))
        blocked = (raw.get("promptFeedback") or {}).get("blockReason")
        finish = "refusal" if blocked else ("tool" if calls else FINISH.get(reason, "other" if reason else "stop"))
        u = raw.get("usageMetadata") or {}
        ext = {"finish_reason": reason, "block_reason": blocked, "thoughts_tokens": u.get("thoughtsTokenCount"),
               "tool_use_prompt_tokens": u.get("toolUsePromptTokenCount")}
        err = (raw.get("error") or {}).get("message", "") if raw.get("error") else ""
        return CanonicalResponse(self.name, raw.get("modelVersion") or self.model, "".join(text), calls,
                                 "error" if err else finish, self.normalize_usage(raw),
                                 extensions={self.name: ext}, error=err)

    def _stream_parse(self, events):
        parts, last = [], {}
        for data in events:
            chunk = json.loads(data)
            last = chunk
            for c in chunk.get("candidates") or []:
                for part in (c.get("content") or {}).get("parts") or []:
                    parts.append(part)
                    if "text" in part and not part.get("thought"):
                        yield part["text"]
        merged = dict(last)
        if merged.get("candidates"):
            cand = dict(merged["candidates"][0])
            cand["content"] = {"role": "model", "parts": parts}
            merged["candidates"] = [cand]
        return merged
