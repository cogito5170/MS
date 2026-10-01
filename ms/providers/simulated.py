"""모의 transport -- **배선 확인 전용.** 진짜 provider 의 결과가 아니다.

각 provider 의 **제 말투(dialect)** 그대로 날것 응답을 지어 돌려준다 -- OpenAI 면 `output[...]` · `usage.input_tokens_details`,
Claude 면 `content[...]` · `cache_read_input_tokens`, Gemini 면 `candidates[...]` · `usageMetadata`. 그래서 진짜 어댑터의 정규화 ·
스트림 파서가 네트워크 없이 끝까지 돈다.

토큰 수는 **지어낸 것**이다(글자 수 / 4). 지연도 지어낸 것이다(고정값). 이 transport 로 나온 평가는 가설의 증거가 아니다 --
`ms.eval` 보고서가 머리에 그것을 적는다.

응답을 정하는 `responder(system, user) -> 글` 은 바꿔 꽂는다. 기본(`toy_agent`)은 프롬프트 정책을 **보지 않는다**(예시 수 ·
지시 길이에 반응하지 않는다) -- 모의가 가설의 답을 미리 품지 않게.
"""
from __future__ import annotations

import json
import re

RISK_ORDER = {"read": 0, "local": 1, "external": 2, "irreversible": 3}


def _state_of(user: str):
    m = re.search(r"STATE:\n(.*)", user, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1).strip().splitlines()[0])
    except (ValueError, IndexError):
        return {}


def _visible_ids(st: dict) -> set:
    ids = set()
    for row in st.get("state") or []:
        if isinstance(row, dict) and "id" in row:
            ids.add(row["id"])
        if isinstance(row, dict) and "rows" in row and "cols" in row:       # COMPRESS 표
            i = row["cols"].index("id") if "id" in row["cols"] else 0
            ids |= {r[i] for r in row["rows"]}
    return ids


def toy_agent(system: str, user: str) -> str:
    """맥락만 보고 제안 하나를 낸다. 과업 글에 도구 이름 · 개체 id 가 있으면 그것을 고른다."""
    st = _state_of(user)
    task = st.get("task", "")
    tools = [t for t in st.get("tools") or [] if t.get("name") != "retrieve"]
    retr = next((t for t in st.get("tools") or [] if t.get("name") == "retrieve"), None)
    denied = {(d.get("tool"), d.get("target")) for d in st.get("denied") or []}
    seen = _visible_ids(st)
    wanted = [w for w in re.findall(r"[a-z]+\d+", task)]
    if retr and any(w not in seen for w in wanted):                          # 과업이 말한 개체가 안 보이면 꺼낸다
        for h in retr.get("targets") or []:
            if ("retrieve", h) not in denied:
                return json.dumps({"tool": "retrieve", "target": h, "args": {}, "rationale": "과업의 개체가 요약 속에 있다"})
    named = [t for t in tools if t["name"] in task]
    pool = named or sorted(tools, key=lambda t: RISK_ORDER.get(t.get("risk"), 9))
    for t in pool:
        targets = [x for x in t.get("targets") or [] if (t["name"], x) not in denied]
        pick = next((x for x in wanted if x in targets), targets[0] if targets else None)
        if pick is None:
            continue
        args = {}
        for k, ps in (t.get("params") or {}).items():
            args[k] = ps.get("min", 1) if ps.get("type") in ("integer", "number") else "auto"
        return json.dumps({"tool": t["name"], "target": pick, "args": args, "rationale": "모의"}, ensure_ascii=False)
    return json.dumps({"tool": "none", "rationale": "할 것이 없다"})


def _toks(s: str) -> int:
    return max(1, len(s) // 4)


class SimulatedTransport:
    """dialect: openai · claude · gemini. 진짜 transport 와 같은 `post` · `stream` 을 갖는다."""

    def __init__(self, dialect: str, responder=toy_agent, latency_ms: float = 0.0):
        self.dialect, self.responder, self.latency_ms = dialect, responder, latency_ms
        self.requests: list = []

    def _texts(self, body):
        if self.dialect == "openai":
            return body.get("instructions", ""), body["input"][0]["content"]
        if self.dialect == "claude":
            return body.get("system", ""), body["messages"][0]["content"]
        return body["systemInstruction"]["parts"][0]["text"], body["contents"][0]["parts"][0]["text"]

    def _answer(self, body):
        self.requests.append(body)
        system, user = self._texts(body)
        out = self.responder(system, user)
        if self.dialect in ("openai", "claude") and (body.get("text") or body.get("output_config", {}).get("format")):
            obj = json.loads(out)                                   # 스키마 모드: args 는 JSON 문자열
            obj = {"tool": obj.get("tool", "none"), "target": obj.get("target", ""),
                   "args": json.dumps(obj.get("args") or {}), "rationale": obj.get("rationale", "")}
            out = json.dumps(obj, ensure_ascii=False)
        return out, _toks(system + user), _toks(out)

    def raw(self, body) -> dict:
        text, i, o = self._answer(body)
        m = body.get("model", "sim")
        if self.dialect == "openai":
            return {"model": m, "status": "completed",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                    "usage": {"input_tokens": i, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": o,
                              "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": i + o}}
        if self.dialect == "claude":
            return {"model": m, "type": "message", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": text}],
                    "usage": {"input_tokens": i, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                              "output_tokens": o}}
        return {"modelVersion": m, "candidates": [{"finishReason": "STOP",
                                                   "content": {"role": "model", "parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": i, "candidatesTokenCount": o, "totalTokenCount": i + o}}

    def post(self, url, headers, body) -> dict:
        return self.raw(body)

    def stream(self, url, headers, body):
        r = self.raw(body)
        if self.dialect == "openai":
            text = r["output"][0]["content"][0]["text"]
            for k in range(0, len(text), 16):
                yield json.dumps({"type": "response.output_text.delta", "delta": text[k:k + 16]})
            yield json.dumps({"type": "response.completed", "response": r})
        elif self.dialect == "claude":
            text, u = r["content"][0]["text"], r["usage"]
            yield json.dumps({"type": "message_start", "message": {"model": r["model"], "usage": dict(u, output_tokens=1)}})
            yield json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            for k in range(0, len(text), 16):
                yield json.dumps({"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "text_delta", "text": text[k:k + 16]}})
            yield json.dumps({"type": "content_block_stop", "index": 0})
            yield json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                              "usage": {"output_tokens": u["output_tokens"]}})
            yield json.dumps({"type": "message_stop"})
        else:
            text = r["candidates"][0]["content"]["parts"][0]["text"]
            pieces = [text[k:k + 16] for k in range(0, len(text), 16)] or [""]
            for n, piece in enumerate(pieces):
                chunk = {"modelVersion": r["modelVersion"],
                         "candidates": [{"content": {"role": "model", "parts": [{"text": piece}]}}]}
                if n == len(pieces) - 1:
                    chunk["candidates"][0]["finishReason"] = "STOP"
                    chunk["usageMetadata"] = r["usageMetadata"]
                yield json.dumps(chunk)
