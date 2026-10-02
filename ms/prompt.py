"""Prompt Policy -- 최소 맥락 + 프롬프트 계획 -> CanonicalPrompt. Context Policy 와 따로다.

Context Policy 는 **무엇을** 보일지(어느 행 · 요약 · 손잡이)를, Prompt Policy 는 그것을 **어떻게** 말할지를 정한다:

    instruction_mode   full · concise
    context_mode       json(그대로) · labeled(칸 뜻 한 줄을 앞에 붙임)
    example_count      0 · 1 · 2 (고정된 예시 은행에서 앞에서부터)
    reasoning          None · off · low · medium · high   -> CanonicalRequest.inference (provider 가 못 하면 unsupported)
    max_output_tokens  정수                                -> CanonicalRequest.inference
    output_format      json_text(글 속 JSON) · json_schema(provider 가 할 수 있으면 native 강제, 아니면 프롬프트로)
    tool_permission    offered · no_irreversible · read_local -- 맥락이 제안한 도구를 **좁히기만** 한다

Prompt Policy 는 WALP 를 모른다(import 하지 않는다). 지시문에 무엇을 써도 중재자의 허가 · 규칙은 바뀌지 않는다.
"""
from __future__ import annotations

from .canonical import PROPOSAL_SCHEMA, CanonicalPrompt
from .llm import INSTRUCTIONS
from .policy import FIXED_PROMPT, TOOL_PERMISSION

CONCISE = """제안만 한다(실행은 중재자). STATE 는 질의 결과뿐이다.
tools 의 도구 · targets 의 대상만. 요약 속 개체는 retrieve 먼저. denied 를 고친다. 할 것 없으면 "none".
JSON 하나: {"tool","target","args","rationale":"한 줄"}"""

# 지시문 글의 판본 -- 계획(policy.py 의 판본)과 따로 센다. 계획이 같아도 글이 바뀌면 이것을 올린다.
#   prompt-text-1  concise 가 원래 지시의 "rationale 한 줄" 을 빠뜨렸다(2026-10-01 claude-cli 실행에서 출력이 늘어난 후보 원인)
#   prompt-text-2  concise 에 "한 줄" 을 되돌렸다 (배치 legacy)
#   prompt-text-3  배치 stable_prefix -- 시스템 글을 모든 계획이 같이 쓰는 한 덩어리로 고정하고, 계획이 바꾸는 것은
#                  전부 사용자 글의 STATE **뒤**(tail)로 옮겼다. provider 캐시는 앞부분 일치라서(도구 -> 시스템 -> 메시지)
#                  계획이 시스템 글을 바꾸면 캐시가 깨진다(재측정 2). concise 는 이 배치에서 적용하지 않는다(notes)
TEMPLATE_VERSIONS = {"legacy": "prompt-text-2", "stable_prefix": "prompt-text-3"}
DEFAULT_LAYOUT = "stable_prefix"
TEMPLATE_VERSION = TEMPLATE_VERSIONS[DEFAULT_LAYOUT]

LEGEND = ("칸: state=본 행(_q 질의 · _stale 낡은 속성 · 표 꼴이면 cols/rows), summaries=요약된 행, "
          "handles=retrieve 로 꺼낼 묶음(deferred=미룬 질의), dropped=정책이 뺀 행 수, denied=직전에 막힌 제안")

EXAMPLES = [
    {"input": 'STATE: {"task":"뜨거운 노드를 식혀라","state":[{"id":"n1","temp":95,"status":"critical"}],'
              '"tools":[{"name":"cool","risk":"local","targets":["n1"],"params":{"level":{"type":"integer","min":1,"max":3}}}]}',
     "output": '{"tool":"cool","target":"n1","args":{"level":2},"rationale":"n1 이 critical"}'},
    {"input": 'STATE: {"task":"n7 을 고쳐라","state":[],"summaries":[{"query":"all","handle":"h1"}],'
              '"handles":{"h1":{"query":"all","count":9}},"tools":[{"name":"retrieve","risk":"read","targets":["h1"]}]}',
     "output": '{"tool":"retrieve","target":"h1","args":{},"rationale":"n7 이 요약 속에 있다"}'},
]

ARGS_NOTE = '\nargs 는 JSON 객체를 담은 **문자열**로 낸다(예: "{\\"level\\": 2}").'
ARGS_EITHER = "args 는 JSON 객체다. 출력 스키마가 주어지면 그 객체를 담은 문자열로 낸다."
# stable_prefix 의 시스템 글 -- 어떤 계획 · 상태에서도 바이트가 같다
STABLE_SYSTEM = f"{INSTRUCTIONS}\n{LEGEND}\n{ARGS_EITHER}"

POLICY_LINE = {"offered": "", "no_irreversible": "되돌릴 수 없는(irreversible) 도구는 제안하지 마라.",
               "read_local": "read · local 도구만 제안하라."}


def plan_inference(plan: dict) -> dict:
    out = {}
    if plan.get("reasoning"):
        out["reasoning"] = plan["reasoning"]
    if plan.get("max_output_tokens"):
        out["max_output_tokens"] = int(plan["max_output_tokens"])
    return out


class PromptPolicy:
    """layout:
        stable_prefix (기본) 시스템 글 = STABLE_SYSTEM 고정. 예시는 STATE 앞, 계획별 지침(도구 허용 등)은 STATE 뒤.
                      instruction_mode=concise 는 적용하지 않고 notes 에 적는다(지시문을 줄이면 앞부분이 바뀐다).
        legacy        prompt-text-2 그대로: 계획이 시스템 글을 바꾼다(concise · labeled · 스키마 메모 · 도구 허용 줄).
    """

    def __init__(self, layout: str = DEFAULT_LAYOUT):
        if layout not in TEMPLATE_VERSIONS:
            raise ValueError(f"모르는 배치 {layout!r}")
        self.layout = layout
        self.version = TEMPLATE_VERSIONS[layout]

    def build(self, ctx, plan: "dict | None" = None, preamble: str = "") -> CanonicalPrompt:
        plan = dict(FIXED_PROMPT, **(plan or {}))
        allowed = set(TOOL_PERMISSION[plan["tool_permission"]])     # KeyError 면 모르는 값 -- 넓히는 값은 없다
        ctx.restrict_tools(allowed)
        payload = ctx.payload()
        context = [{"type": "state", "data": payload}]
        schema = PROPOSAL_SCHEMA if plan["output_format"] == "json_schema" else None
        examples = EXAMPLES[: int(plan["example_count"])]
        if self.layout == "legacy":
            instruction = INSTRUCTIONS if plan["instruction_mode"] == "full" else CONCISE
            if plan["context_mode"] == "labeled":
                instruction = f"{instruction}\n{LEGEND}"
            if plan["output_format"] == "json_schema":
                instruction += ARGS_NOTE
            return CanonicalPrompt(instruction=instruction, context=context, examples=examples, output_schema=schema,
                                   tool_policy=POLICY_LINE[plan["tool_permission"]], tools=payload["tools"],
                                   preamble=preamble)
        notes = []
        if plan["instruction_mode"] != "full":
            notes.append(f"instruction_mode={plan['instruction_mode']}(stable_prefix 에서는 시스템 글을 안 바꾼다)")
        tail = [x for x in (POLICY_LINE[plan["tool_permission"]],) if x]
        return CanonicalPrompt(instruction=STABLE_SYSTEM, context=context, examples=examples, output_schema=schema,
                               tools=payload["tools"], preamble=preamble, tail=tail, notes=notes)
