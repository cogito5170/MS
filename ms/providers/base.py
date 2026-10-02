"""LLMProvider -- canonical 요청을 provider 의 요청으로, provider 의 응답을 canonical 응답으로.

    to_provider_request(req) -> (url, headers, body, unsupported)   순수 함수. 네트워크 없이 시험한다
    generate(req)            -> CanonicalResponse
    stream(req)              -> 조각(str) 을 내고, 끝에 CanonicalResponse 를 돌려준다(StopIteration.value)
    normalize_response(raw)  -> CanonicalResponse
    normalize_usage(raw)     -> Usage

provider 의 날것(raw dict · SSE 줄)은 이 계층 밖으로 안 나간다. 위로 가는 것은 CanonicalResponse 뿐이고, provider 고유 값은
`extensions[<이름>]` 에 담긴다.

네트워크는 `transport` 가 맡는다(기본: 표준 라이브러리 HTTP). 시험은 녹음된 응답을 돌려주는 transport 를 꽂는다. 이 패키지는
SDK 를 쓰지 않는다 -- 의존성 0 이 이 저장소의 규칙이고, SDK 객체가 위로 샐 길도 없앤다.

**지원 안 하는 추론 옵션을 흉내 내지 않는다.** 어댑터가 모형이 못 하는 옵션을 받으면 그 옵션을 provider 요청에 **넣지 않고**
`unsupported` 에 적는다. 그것이 텔레메트리 확장 칸으로 간다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from ..canonical import CanonicalRequest, CanonicalResponse, Usage


class ProviderError(RuntimeError):
    """status · body · headers 는 provider 가 **준** 것(HTTP 오류일 때만). 없으면 None -- 메시지 글에서 꺼내지 않는다.
    L0 Telemetry 의 llm.error 가 이것을 그대로 읽는다(ms/l0.py)."""

    def __init__(self, msg: str, status: "int | None" = None, body: "dict | None" = None,
                 headers: "dict | None" = None):
        super().__init__(msg)
        self.status, self.body, self.headers = status, body, headers


def _http_error(e) -> ProviderError:
    raw = e.read().decode(errors="replace")
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return ProviderError(f"HTTP {e.code}: {raw[:500]}", e.code, body if isinstance(body, dict) else None,
                         dict(e.headers.items()) if e.headers else None)


class HttpTransport:
    """표준 라이브러리 HTTP. `post` 는 dict, `stream` 은 SSE 의 data 줄(문자열)을 하나씩 낸다."""

    def __init__(self, timeout: float = 300):
        self.timeout = timeout

    def _req(self, url, headers, body):
        return urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")

    def post(self, url, headers, body) -> dict:
        try:
            with urllib.request.urlopen(self._req(url, headers, body), timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise _http_error(e) from None
        except urllib.error.URLError as e:
            raise ProviderError(f"연결 실패: {e.reason}") from None

    def stream(self, url, headers, body):
        try:
            with urllib.request.urlopen(self._req(url, headers, body), timeout=self.timeout) as r:
                for raw in r:
                    line = raw.decode().rstrip("\r\n")
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            yield data
        except urllib.error.HTTPError as e:
            raise _http_error(e) from None
        except urllib.error.URLError as e:
            raise ProviderError(f"연결 실패: {e.reason}") from None


class LLMProvider:
    name = "base"
    env_key = ""                 # 키를 읽을 환경 변수
    supports_stream = True
    simulated = False

    def __init__(self, model: str, api_key: "str | None" = None, transport=None, clock=time.perf_counter):
        self.model = model
        self.api_key = api_key if api_key is not None else os.environ.get(self.env_key, "")
        self.transport = transport or HttpTransport()
        self.clock = clock

    # -- 어댑터가 채운다 ------------------------------------------------------
    def capabilities(self) -> dict:
        """이 provider · 모형이 **무엇을 어떻게** 하는지. 평가 보고서 머리에 그대로 붙는다."""
        raise NotImplementedError

    def to_provider_request(self, req: CanonicalRequest):
        raise NotImplementedError

    def normalize_usage(self, raw) -> Usage:
        raise NotImplementedError

    def normalize_response(self, raw) -> CanonicalResponse:
        raise NotImplementedError

    def _stream_parse(self, events):
        """SSE data(문자열) 열 -> (조각 낸다, 끝에 raw 응답에 해당하는 dict 를 돌려준다). 스트리밍을 지원하면 채운다."""
        raise NotImplementedError

    # -- 공통 ---------------------------------------------------------------
    def _need_key(self):
        if not self.api_key:
            raise ProviderError(f"{self.name}: 키가 없다(환경 변수 {self.env_key})")

    def generate(self, req: CanonicalRequest) -> CanonicalResponse:
        self._need_key()
        url, headers, body, unsupported = self.to_provider_request(req)
        t0 = self.clock()
        raw = self.transport.post(url, headers, body)
        out = self.normalize_response(raw)
        out.inference_ms = out.inference_ms if out.inference_ms is not None else (self.clock() - t0) * 1000
        out.unsupported = unsupported + out.unsupported
        return out

    def stream(self, req: CanonicalRequest):
        self._need_key()
        req = CanonicalRequest(req.model, req.prompt, req.inference, req.tool_mode, True, req.metadata)
        url, headers, body, unsupported = self.to_provider_request(req)
        t0 = self.clock()
        ttft = None
        gen = self._stream_parse(self.transport.stream(url, headers, body))
        try:
            while True:
                piece = next(gen)
                if ttft is None:
                    ttft = (self.clock() - t0) * 1000
                yield piece
        except StopIteration as stop:
            raw = stop.value
        out = self.normalize_response(raw)
        out.ttft_ms = ttft
        out.inference_ms = (self.clock() - t0) * 1000
        out.unsupported = unsupported + out.unsupported
        return out

    def collect(self, req: CanonicalRequest) -> CanonicalResponse:
        """stream() 을 끝까지 돌려 CanonicalResponse 를 받는다."""
        gen = self.stream(req)
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value


def parse_args_json(s):
    """함수 호출 인자(문자열 JSON)를 dict 로. 못 읽으면 None."""
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s or "{}")
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def native_tools(prompt) -> list:
    """제안을 provider 의 함수 호출로 받을 때의 canonical 함수 정의. 도구 이름마다 하나, 인자는 target 과 args(JSON 문자열)."""
    out = []
    for t in prompt.tools:
        out.append({"name": t["name"], "description": t.get("description", ""),
                    "parameters": {"type": "object",
                                   "properties": {"target": {"type": "string", "enum": list(t.get("targets", []))},
                                                  "args": {"type": "string"}},
                                   "required": ["target", "args"], "additionalProperties": False}})
    return out
