"""Canonical 꼴 -- provider 에 매이지 않는 요청 · 응답 · 사용량.

MS 의 정책 층(Context · Prompt · Provider Policy · Arbiter · State)은 이 꼴만 안다. OpenAI · Claude · Gemini 의 요청 · 응답 모양은
`ms/providers/` 안에서만 산다(원칙 10). 그래서 여기 있는 것은 전부 **기본 타입**(str · int · float · bool · None · list · dict)이다 --
provider SDK 객체가 들어올 자리가 없다.

    CanonicalPrompt   무엇을 말할지   instruction · context · examples · output_schema · tool_policy · tools
    CanonicalRequest  어떻게 부를지   model · prompt · inference(canonical 옵션) · stream · metadata
    CanonicalResponse 무엇이 왔나     text · tool_calls · finish · usage · latency · unsupported · extensions
    Usage             토큰           OpenTelemetry GenAI 규약을 따른다 -- input_tokens 는 **캐시 읽기를 포함한** 전체 입력

`extensions` 는 provider 고유의 값(Claude 의 cache_creation · OpenAI 의 reasoning_tokens · Gemini 의 thoughts)을 담는 자리다.
canonical 칸에 억지로 넣지 않는다. State 의 모형은 extensions 를 읽지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# Prompt Policy 가 고를 수 있는 추론 설정. provider 가 못 하는 것은 어댑터가 `unsupported` 로 돌려준다 -- 흉내 내지 않는다
REASONING_LEVELS = ("off", "low", "medium", "high")

# 제안의 canonical 꼴. json_schema 모드에서 args 는 **JSON 문자열**이다 -- 엄격 스키마는 열린 객체를 못 받으므로(OpenAI strict ·
# Claude output_config 둘 다 additionalProperties:false 를 요구한다) 어느 provider 에서나 같은 스키마를 쓰려고
PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "target": {"type": "string"},
        "args": {"type": "string", "description": "JSON object as a string, e.g. {\"level\": 2}. \"{}\" if none"},
        "rationale": {"type": "string"},
    },
    "required": ["tool", "target", "args", "rationale"],
    "additionalProperties": False,
}


@dataclass
class CanonicalPrompt:
    instruction: str
    context: list = field(default_factory=list)       # [{"type": "state", "data": {...}}] -- 최소 맥락만
    examples: list = field(default_factory=list)      # [{"input": "...", "output": "..."}]
    output_schema: "dict | None" = None               # None 이면 글 속 JSON
    tool_policy: str = ""                             # 사람이 읽는 한 줄(무엇을 제안해도 되나). Arbiter 를 바꾸지 않는다
    tools: list = field(default_factory=list)         # 도구 카드 -- 맥락이 제안한 것의 부분집합
    preamble: str = ""                                # 사용자 글 맨 앞(실행마다 다른 표지 -- 평가의 --fresh)
    tail: list = field(default_factory=list)          # STATE 뒤에 붙는 계획별 지침(캐시를 지키려고 뒤에 둔다)
    notes: list = field(default_factory=list)         # 계획이 요청했지만 이 배치에서 적용하지 않은 것
    # CR 의 선언: 어디까지가 요청 사이에 바뀌지 않는 앞부분인가. "system" = 시스템 글 전체가 안정.
    # None = 선언 없음(안정을 약속하지 않는다). provider 가 이것을 **어떻게** 캐시할지는 어댑터가 정한다 -- CR 은 provider 를 모른다.
    cache_boundary: "str | None" = None

    def context_text(self) -> str:
        return "\n".join(json.dumps(c["data"], ensure_ascii=False, separators=(",", ":")) for c in self.context)

    def system_text(self) -> str:
        return self.instruction + (f"\n{self.tool_policy}" if self.tool_policy else "")

    def user_text(self) -> str:
        parts = []
        if self.preamble:
            parts.append(self.preamble)
        if self.examples:
            parts.append("EXAMPLES:\n" + "\n".join(f"{e['input']}\n=> {e['output']}" for e in self.examples))
        parts.append(f"STATE:\n{self.context_text()}\n")
        if self.tail:
            parts.append("\n".join(self.tail) + "\n")
        return "\n\n".join(parts)

    def text(self) -> str:
        """글 하나로 받는 LLM(명령줄 · 함수)용. 고정 정책에서는 예전 `build_prompt(ctx)` 와 바이트까지 같다."""
        return f"{self.system_text()}\n\n{self.user_text()}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CanonicalRequest:
    model: str
    prompt: CanonicalPrompt
    inference: dict = field(default_factory=dict)     # {"reasoning": "low", "max_output_tokens": 1024}
    tool_mode: str = "text"                           # text: 제안을 글 속 JSON 으로 · native: provider 의 함수 호출로
    stream: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"model": self.model, "instruction": self.prompt.system_text(), "context": self.prompt.context,
                "examples": self.prompt.examples, "tools": self.prompt.tools, "output_schema": self.prompt.output_schema,
                "inference": self.inference, "tool_mode": self.tool_mode, "stream": self.stream,
                "metadata": self.metadata}


@dataclass
class Usage:
    input_tokens: "int | None" = None          # 캐시 읽기 포함 전체 입력(OTel gen_ai.usage.input_tokens)
    output_tokens: "int | None" = None         # 추론 · 사고 토큰 포함(청구 기준)
    cached_input_tokens: "int | None" = None   # input_tokens 중 캐시에서 읽은 몫
    total_tokens: "int | None" = None

    def __add__(self, o: "Usage") -> "Usage":
        def s(a, b):
            return None if a is None and b is None else (a or 0) + (b or 0)
        return Usage(s(self.input_tokens, o.input_tokens), s(self.output_tokens, o.output_tokens),
                     s(self.cached_input_tokens, o.cached_input_tokens), s(self.total_tokens, o.total_tokens))


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class CanonicalResponse:
    provider: str
    model: str
    text: str = ""
    tool_calls: list = field(default_factory=list)     # [ToolCall] -- **제안**이다. 실행이 아니다
    finish: str = "stop"                               # stop · length · tool · refusal · error · other
    usage: Usage = field(default_factory=Usage)
    ttft_ms: "float | None" = None                     # 스트리밍일 때만. 비스트리밍이면 None -- 총 시간으로 메우지 않는다
    inference_ms: "float | None" = None                # provider 호출 벽시계(네트워크 포함) 또는 provider 가 보고한 값
    cost_usd: "float | None" = None                    # provider 가 보고했을 때만(claude-cli). 아니면 가격표로 나중에
    unsupported: list = field(default_factory=list)    # 요청했지만 이 provider/모형이 못 해서 **안 보낸** 옵션
    extensions: dict = field(default_factory=dict)     # {"<provider>": {...}} -- canonical 에 없는 provider 고유 값
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_calls"] = [asdict(t) for t in self.tool_calls]
        return d
