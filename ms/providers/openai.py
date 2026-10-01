"""OpenAI -- Responses API (`POST /v1/responses`).

canonical -> OpenAI:
    system_text      -> instructions
    user_text        -> input[0] (role user)
    output_schema    -> text.format {type: json_schema, name, schema, strict}
    reasoning        -> reasoning.effort (off -> "none"). **모형에 달렸다** -- 추론 모형이 아니면 API 가 거절한다.
                        어댑터는 모형 목록을 추측하지 않는다(capabilities 에 model-dependent 로 적는다)
    max_output_tokens-> max_output_tokens (추론 토큰 포함 상한)
    native tools     -> tools [{type: function, name, description, parameters, strict}]

OpenAI -> canonical usage (OTel 규약):
    input_tokens                         -> input_tokens        (캐시 포함 -- 그대로)
    input_tokens_details.cached_tokens   -> cached_input_tokens
    output_tokens                        -> output_tokens       (reasoning 포함 -- 그대로)
    output_tokens_details.reasoning_tokens -> extensions.openai.reasoning_tokens
"""
from __future__ import annotations

import json

from ..canonical import CanonicalResponse, ToolCall, Usage
from .base import LLMProvider, native_tools, parse_args_json

URL = "https://api.openai.com/v1/responses"
EFFORT = {"off": "none", "low": "low", "medium": "medium", "high": "high"}


class OpenAIProvider(LLMProvider):
    name = "openai"
    env_key = "OPENAI_API_KEY"

    def capabilities(self) -> dict:
        return {"provider": self.name, "model": self.model, "api": "responses",
                "output_schema": "native (text.format json_schema, strict)",
                "reasoning": "model-dependent (reasoning.effort; non-reasoning models reject it)",
                "cached_tokens": "reported (input_tokens_details.cached_tokens, included in input_tokens)",
                "ttft": "stream only", "stream": True, "native_tools": True, "cost": "not reported (price table)"}

    def to_provider_request(self, req):
        p, unsupported = req.prompt, []
        body = {"model": req.model or self.model, "instructions": p.system_text(),
                "input": [{"role": "user", "content": p.user_text()}], "store": False}
        inf = req.inference or {}
        if inf.get("max_output_tokens"):
            body["max_output_tokens"] = int(inf["max_output_tokens"])
        if inf.get("reasoning"):
            body["reasoning"] = {"effort": EFFORT[inf["reasoning"]]}
        if p.output_schema is not None and req.tool_mode == "text":
            body["text"] = {"format": {"type": "json_schema", "name": "proposal", "schema": p.output_schema,
                                       "strict": True}}
        if req.tool_mode == "native" and p.tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"],
                              "parameters": t["parameters"], "strict": True} for t in native_tools(p)]
        if req.stream:
            body["stream"] = True
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        return URL, headers, body, unsupported

    def normalize_usage(self, raw) -> Usage:
        u = (raw or {}).get("usage") or {}
        i, o = u.get("input_tokens"), u.get("output_tokens")
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens")
        total = u.get("total_tokens")
        if total is None and i is not None and o is not None:
            total = i + o
        return Usage(i, o, cached, total)

    def normalize_response(self, raw) -> CanonicalResponse:
        raw = raw or {}
        text, calls, kinds = [], [], []
        for item in raw.get("output") or []:
            kinds.append(item.get("type"))
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        text.append(c.get("text", ""))
                    elif c.get("type") == "refusal":
                        text.append(c.get("refusal", ""))
            elif item.get("type") == "function_call":
                args = parse_args_json(item.get("arguments"))
                calls.append(ToolCall(item.get("name", ""), args if args is not None else {"_unparsed": True}))
        status = raw.get("status")
        refused = any(c.get("type") == "refusal" for it in raw.get("output") or [] if it.get("type") == "message"
                      for c in it.get("content") or [])
        if refused:
            finish = "refusal"
        elif calls:
            finish = "tool"
        elif status == "incomplete":
            finish = "length" if (raw.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else "other"
        elif status in (None, "completed"):
            finish = "stop"
        else:
            finish = "error" if status == "failed" else "other"
        u = raw.get("usage") or {}
        ext = {"status": status, "output_item_types": kinds,
               "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens")}
        return CanonicalResponse(self.name, raw.get("model") or self.model, "".join(text), calls, finish,
                                 self.normalize_usage(raw), extensions={self.name: ext},
                                 error=((raw.get("error") or {}).get("message") or "") if raw.get("error") else "")

    def _stream_parse(self, events):
        final = None
        for data in events:
            ev = json.loads(data)
            t = ev.get("type")
            if t == "response.output_text.delta":
                yield ev.get("delta", "")
            elif t in ("response.completed", "response.incomplete", "response.failed"):
                final = ev.get("response")
        return final or {}
