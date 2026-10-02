"""Claude -- Messages API (`POST /v1/messages`).

canonical -> Claude:
    system_text      -> system
    user_text        -> messages[0] (role user)
    output_schema    -> output_config.format {type: json_schema, schema}
    reasoning        -> output_config.effort (low · medium · high)
                        · Haiku 4.5 는 effort 를 **거절**한다 -> unsupported
                        · "off": Opus 5.5 · Fable 는 사고를 끌 수 없다(400). Sonnet 5.5 는 between_tools 라는 다른 것이다 ->
                          어느 모형에서도 흉내 내지 않고 unsupported
                        · 안 주면 모형 기본(Opus 5.5 의 기본은 medium)
    max_output_tokens-> max_tokens (없으면 16000)
    native tools     -> tools [{name, description, input_schema, strict}] + tool_choice auto
                        (Opus 5.5 · Sonnet 5.5 · Fable 5.1 은 강제 tool_choice 를 거절한다 -- auto 만 쓴다)

Claude -> canonical usage (OTel 규약):
    input_tokens + cache_read_input_tokens + cache_creation_input_tokens -> input_tokens
        (Claude 의 input_tokens 는 캐시를 **뺀** 몫이다. 그대로 쓰면 OpenAI 와 뜻이 달라진다)
    cache_read_input_tokens       -> cached_input_tokens
    output_tokens                 -> output_tokens (사고 포함)
    cache_creation_input_tokens   -> extensions.claude.cache_creation_input_tokens
"""
from __future__ import annotations

import json

from ..canonical import CanonicalResponse, ToolCall, Usage
from .base import LLMProvider, native_tools

URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
NO_EFFORT = ("claude-haiku-4-5",)


def usage_from(u: dict) -> Usage:
    u = u or {}
    base, rd, cr = u.get("input_tokens"), u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens")
    i = None if base is None else base + (rd or 0) + (cr or 0)
    o = u.get("output_tokens")
    return Usage(i, o, rd if rd is not None else (0 if base is not None else None),
                 None if i is None or o is None else i + o)


def finish_of(stop_reason) -> str:
    return {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool",
            "refusal": "refusal", "pause_turn": "other"}.get(stop_reason, "other")


class ClaudeProvider(LLMProvider):
    name = "claude"
    env_key = "ANTHROPIC_API_KEY"

    def capabilities(self) -> dict:
        eff = "unsupported (model rejects effort)" if self.model.startswith(NO_EFFORT) else \
            "output_config.effort low/medium/high; 'off' unsupported"
        return {"provider": self.name, "model": self.model, "api": "messages",
                "output_schema": "native (output_config.format json_schema)",
                "reasoning": eff,
                "cached_tokens": "reported separately (cache_read/creation), summed into input_tokens",
                "prefix_cache": "breakpoint at end of system text when CR declares cache_boundary=system "
                                "(min 512 tokens on Opus/Sonnet 5.5)",
                "ttft": "stream only", "stream": True, "native_tools": True, "cost": "not reported (price table)"}

    def to_provider_request(self, req):
        p, inf, unsupported = req.prompt, req.inference or {}, []
        model = req.model or self.model
        # 캐시 지점은 시스템 글 **끝**에 둔다. Claude 의 캐시는 지점에서만 항목을 만들고 앞부분 일치로 읽는다 -- 지점이
        # 메시지 끝에만 있으면 사용자 글이 바뀔 때마다 전부 새로 쓴다(claude -p 에서 실측: 같은 시스템 · 다른 사용자 글 -> 읽기 0).
        # 모형별 최소 길이(Opus 5.5 · Sonnet 5.5: 512 토큰)보다 짧으면 조용히 안 걸린다.
        # CR 이 "여기까지 안정" 이라고 선언했을 때만(cache_boundary="system") 지점을 둔다 -- 선언이 없는데 지점을 두면
        # 매번 바뀌는 글에 캐시 쓰기(1.25~2 배)만 낸다.
        block = {"type": "text", "text": p.system_text()}
        if p.cache_boundary == "system":
            block["cache_control"] = {"type": "ephemeral"}
        body = {"model": model, "max_tokens": int(inf.get("max_output_tokens") or 16000), "system": [block],
                "messages": [{"role": "user", "content": p.user_text()}]}
        oc = {}
        r = inf.get("reasoning")
        if r:
            if r == "off" or model.startswith(NO_EFFORT):
                unsupported.append(f"reasoning={r}")
            else:
                oc["effort"] = r
        if p.output_schema is not None and req.tool_mode == "text":
            oc["format"] = {"type": "json_schema", "schema": p.output_schema}
        if oc:
            body["output_config"] = oc
        if req.tool_mode == "native" and p.tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"],
                              "strict": True} for t in native_tools(p)]
            body["tool_choice"] = {"type": "auto"}
        if req.stream:
            body["stream"] = True
        headers = {"x-api-key": self.api_key, "anthropic-version": VERSION, "content-type": "application/json"}
        return URL, headers, body, unsupported

    def normalize_usage(self, raw) -> Usage:
        return usage_from((raw or {}).get("usage"))

    def normalize_response(self, raw) -> CanonicalResponse:
        raw = raw or {}
        text, calls, kinds = [], [], []
        for b in raw.get("content") or []:
            kinds.append(b.get("type"))
            if b.get("type") == "text":
                text.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                inp = b.get("input")
                calls.append(ToolCall(b.get("name", ""), inp if isinstance(inp, dict) else {"_unparsed": True}))
        u = raw.get("usage") or {}
        ext = {"stop_reason": raw.get("stop_reason"), "content_types": kinds,
               "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
               "stop_details": raw.get("stop_details")}
        err = (raw.get("error") or {}).get("message", "") if raw.get("type") == "error" else ""
        return CanonicalResponse(self.name, raw.get("model") or self.model, "".join(text), calls,
                                 "error" if err else finish_of(raw.get("stop_reason")), self.normalize_usage(raw),
                                 extensions={self.name: ext}, error=err)

    def _stream_parse(self, events):
        msg = {"content": [], "usage": {}}
        blocks = {}
        for data in events:
            ev = json.loads(data)
            t = ev.get("type")
            if t == "message_start":
                m = ev.get("message") or {}
                msg.update({k: m[k] for k in ("id", "model") if k in m})
                msg["usage"].update(m.get("usage") or {})
            elif t == "content_block_start":
                blocks[ev["index"]] = dict(ev.get("content_block") or {})
                if blocks[ev["index"]].get("type") == "tool_use":
                    blocks[ev["index"]]["_json"] = ""
            elif t == "content_block_delta":
                d, b = ev.get("delta") or {}, blocks.setdefault(ev["index"], {"type": "text", "text": ""})
                if d.get("type") == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                    yield d.get("text", "")
                elif d.get("type") == "input_json_delta":
                    b["_json"] = b.get("_json", "") + d.get("partial_json", "")
            elif t == "message_delta":
                msg.update({k: v for k, v in (ev.get("delta") or {}).items() if k in ("stop_reason", "stop_details")})
                msg["usage"].update(ev.get("usage") or {})       # output_tokens 는 누적값이다
            elif t == "error":
                msg["type"], msg["error"] = "error", ev.get("error") or {}
        for i in sorted(blocks):
            b = blocks[i]
            if b.get("type") == "tool_use":
                try:
                    b["input"] = json.loads(b.pop("_json") or "{}")
                except ValueError:
                    b["input"] = None
            msg["content"].append(b)
        return msg
