"""LLM 자리 -- 최소 맥락을 받고 제안(Proposal) 하나를 낸다. 실행하지 않는다.

LLM 은 `prompt -> 글` 인 아무 호출이면 된다:

    CommandLLM(["claude", "-p"])     # 표준입력으로 프롬프트, 표준출력이 답 (gemini -p 도 같다)
    ScriptedLLM([...])               # 시험 · 예시용. 받은 프롬프트를 `prompts` 에 남긴다

LLM 에 가는 글은 `build_prompt(ctx)` 하나에서만 나온다 -- 최소 맥락의 render() 와 규칙 몇 줄뿐이다.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

NONE = "none"

INSTRUCTIONS = """너는 제안만 한다. 실행은 중재자(Arbiter)가 판정한 뒤에 한다.
아래 STATE 는 상태 전체가 아니라 질의 결과다. 거기 없는 개체는 모른다고 보라.
- 도구는 tools 에 있는 것만, 대상은 그 도구의 targets 에 있는 것만 고른다.
- summaries 로만 보이는 개체에 손대려면 먼저 retrieve 로 그 handle 을 청한다.
- denied 가 있으면 그 까닭을 보고 고친다. 할 것이 없으면 tool 을 "none" 으로.
답은 JSON 객체 하나만: {"tool": "...", "target": "...", "args": {...}, "rationale": "한 줄"}"""


def build_prompt(ctx) -> str:
    """고정 계획 · 기본 배치에서 LLM 이 받는 글(글 하나로 받는 LLM 용)."""
    from .prompt import PromptPolicy
    return PromptPolicy().build(ctx).text()


@dataclass
class Proposal:
    tool: "str | None" = None
    target: "str | None" = None
    args: dict = field(default_factory=dict)
    rationale: str = ""
    error: str = ""
    raw: str = ""

    def key(self) -> str:
        return json.dumps([self.tool, self.target, self.args], sort_keys=True, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {"tool": self.tool, "target": self.target, "args": self.args, "rationale": self.rationale,
                **({"error": self.error} if self.error else {})}


def _first_object(text: str):
    """글 속 첫 번째 JSON 객체. 코드 울타리 · 앞뒤 말은 건너뛴다."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def proposal_from(resp) -> Proposal:
    """CanonicalResponse -> Proposal. provider 의 함수 호출도 글 속 JSON 도 **제안**일 뿐이다 -- 여기서 실행되는 것은 없다."""
    if resp.error or resp.finish in ("refusal", "error"):
        return Proposal(error=f"응답 {resp.finish}: {resp.error}"[:300], raw=resp.text)
    if resp.tool_calls:
        c = resp.tool_calls[0]
        a = c.arguments or {}
        return parse_proposal(json.dumps({"tool": c.name, "target": a.get("target"), "args": a.get("args", {}),
                                          "rationale": "(native tool call)"}, ensure_ascii=False))
    return parse_proposal(resp.text)


def parse_proposal(text: str) -> Proposal:
    obj = _first_object(text or "")
    if obj is None:
        return Proposal(error="JSON 객체가 없다", raw=text or "")
    tool, target, args = obj.get("tool"), obj.get("target"), obj.get("args", {})
    if not isinstance(tool, str) or not tool:
        return Proposal(error="tool 이 문자열이 아니다", raw=text)
    if tool != NONE and (not isinstance(target, str) or not target):
        return Proposal(tool, error="target 이 문자열이 아니다", raw=text)
    if args is None:
        args = {}
    if isinstance(args, str):                       # json_schema 모드: args 는 JSON 문자열
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return Proposal(tool, target, error="args 문자열이 JSON 이 아니다", raw=text)
    if not isinstance(args, dict):
        return Proposal(tool, target, error="args 가 객체가 아니다", raw=text)
    return Proposal(tool, target, args, str(obj.get("rationale", ""))[:500], raw=text)


class CommandLLM:
    def __init__(self, argv, timeout: float = 300):
        self.argv, self.timeout = list(argv), timeout

    def __call__(self, prompt: str) -> str:
        p = subprocess.run(self.argv, input=prompt, capture_output=True, text=True, timeout=self.timeout)
        if p.returncode != 0:
            raise RuntimeError(f"{self.argv[0]} 종료 {p.returncode}: {p.stderr.strip()[:300]}")
        return p.stdout


class ScriptedLLM:
    """정해진 답을 차례로 낸다(함수면 프롬프트를 받아 답한다). 받은 프롬프트를 남긴다."""

    def __init__(self, replies):
        self.replies = replies
        self.prompts: list = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if callable(self.replies):
            return self.replies(prompt)
        i = len(self.prompts) - 1
        r = self.replies[i] if i < len(self.replies) else {"tool": NONE}
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
