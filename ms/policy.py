"""정책 -- "현재 State 에서 무엇을 할 것인가". 셋 다 **(기록된 상태, 정책 판본)의 순수 함수**다.

    Context Policy 선택기   상태 -> 맥락 정책의 손잡이(예산 · COMPRESS · DROP · DEFER)
    Prompt Policy 선택기    상태 -> 프롬프트 계획(지시 꼴 · 맥락 꼴 · 예시 수 · 추론 설정 · 출력 꼴 · 도구 허용)
    Provider Policy         상태 · 요청 -> provider · 모형. 지금은 **명시 선택만**(승자를 가정하지 않는다)

정책은 원 측정(토큰 수)을 보지 않는다. `usage_model.snapshot()` 이 준 **파생 상태**(HIGH · MEDIUM · LOW · 모름)만 본다.
모름은 "고정 정책대로" 다.

**품질 · 안전이 먼저다.** 두 적응 선택기는 위에서부터 규칙을 본다. answer_reliability=LOW 나 correction_rate=HIGH 면
**아무것도 줄이지 않는다** -- 토큰을 아끼려고 품질을 깎는 계획은 나오지 않는다. 그리고 어느 계획도 Arbiter 를 바꾸지 못한다:
Prompt Policy 의 tool_permission 은 도구를 **좁히기만** 한다(넓히는 값이 없다).

판본(`version`)이 같고 상태가 같으면 계획이 같다 -- `replay()` 가 실행 기록에서 그것을 다시 계산해 맞춰 본다.
규칙을 바꾸면 판본을 올린다.
"""
from __future__ import annotations

LEVEL = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _lv(state, k):
    return LEVEL.get(state.get(k), -1)


def _quality_first(state) -> list:
    out = []
    if state.get("answer_reliability") == "LOW":
        out.append("answer_reliability=LOW")
    if state.get("correction_rate") == "HIGH":
        out.append("correction_rate=HIGH")
    return out


# -- Context Policy ----------------------------------------------------------------------------------------------
BASE_CONTEXT = {"budget_chars": 3000, "summarize_min": 3, "keep_max": 20, "compress": False, "defer": [],
                "drop": False, "defer_priority_min": None}


class FixedContext:
    version = "ctx-fixed-1"

    def plan(self, state: dict, base: dict) -> dict:
        return {"version": self.version, "params": dict(BASE_CONTEXT, **base), "reasons": ["고정"]}


class AdaptiveContext:
    """ctx-adaptive-1:
        1. 품질 우선(reliability LOW · correction HIGH) -> 고정과 같다
        2. 압력 = max(token_budget_pressure, context_pressure)
             HIGH   -> 예산 ×0.5 · COMPRESS · DROP · 우선순위 2 이상 질의 DEFER
             MEDIUM -> 예산 ×0.75 · COMPRESS
        3. task_complexity=HIGH 면 DROP · DEFER 를 끄고 예산을 ×0.75 밑으로 안 내린다
    """
    version = "ctx-adaptive-1"

    def plan(self, state: dict, base: dict) -> dict:
        p = dict(BASE_CONTEXT, **base)
        b0 = p["budget_chars"]
        q = _quality_first(state)
        if q:
            return {"version": self.version, "params": p, "reasons": ["품질 우선: " + ", ".join(q) + " -> 줄이지 않는다"]}
        reasons = []
        pressure = max(_lv(state, "token_budget_pressure"), _lv(state, "context_pressure"))
        if pressure == 2:
            p.update(budget_chars=int(b0 * 0.5), compress=True, drop=True, defer_priority_min=2)
            reasons.append("압력 HIGH -> 예산 ×0.5 · COMPRESS · DROP · DEFER(우선순위>=2)")
        elif pressure == 1:
            p.update(budget_chars=int(b0 * 0.75), compress=True)
            reasons.append("압력 MEDIUM -> 예산 ×0.75 · COMPRESS")
        else:
            reasons.append("압력 LOW/모름 -> 고정과 같다")
        if state.get("task_complexity") == "HIGH" and (p["drop"] or p["defer_priority_min"] is not None
                                                      or p["budget_chars"] < int(b0 * 0.75)):
            p.update(drop=False, defer_priority_min=None, budget_chars=max(p["budget_chars"], int(b0 * 0.75)))
            reasons.append("task_complexity=HIGH -> DROP · DEFER 끔, 예산 >= ×0.75")
        return {"version": self.version, "params": p, "reasons": reasons}


# -- Prompt Policy -----------------------------------------------------------------------------------------------
FIXED_PROMPT = {"instruction_mode": "full", "context_mode": "json", "example_count": 0, "reasoning": None,
                "max_output_tokens": 1024, "output_format": "json_text", "tool_permission": "offered"}
TOOL_PERMISSION = {"offered": ("read", "local", "external", "irreversible"),
                   "no_irreversible": ("read", "local", "external"),
                   "read_local": ("read", "local")}


class FixedPrompt:
    version = "prompt-fixed-1"

    def plan(self, state: dict) -> dict:
        return {"version": self.version, "plan": dict(FIXED_PROMPT), "reasons": ["고정"]}


class AdaptivePrompt:
    """prompt-adaptive-1:
        1. 품질 우선(reliability LOW · correction HIGH) 또는 retry_pressure=HIGH
             -> 예시 2 · 출력 json_schema · 맥락 labeled · 지시 full. correction HIGH 면 도구를 no_irreversible 로 좁힌다
        2. 아니고 압력(token · context) HIGH -> 지시 concise
        3. latency_pressure=HIGH 이고 complexity!=HIGH 이고 1 이 아니면 -> reasoning low · max_output_tokens 512
        4. complexity=HIGH 이고 latency!=HIGH -> reasoning high
    """
    version = "prompt-adaptive-1"

    def plan(self, state: dict) -> dict:
        p, reasons = dict(FIXED_PROMPT), []
        q = _quality_first(state)
        guard = bool(q) or state.get("retry_pressure") == "HIGH"
        if guard:
            p.update(example_count=2, output_format="json_schema", context_mode="labeled", instruction_mode="full")
            reasons.append("품질 우선(" + ", ".join(q or ["retry_pressure=HIGH"]) + ") -> 예시 2 · json_schema · labeled")
            if state.get("correction_rate") == "HIGH":
                p["tool_permission"] = "no_irreversible"
                reasons.append("correction_rate=HIGH -> irreversible 도구를 제안하지 않게 좁힘")
        elif max(_lv(state, "token_budget_pressure"), _lv(state, "context_pressure")) == 2:
            p["instruction_mode"] = "concise"
            reasons.append("압력 HIGH -> 지시 concise")
        if state.get("latency_pressure") == "HIGH" and state.get("task_complexity") != "HIGH" and not guard:
            p.update(reasoning="low", max_output_tokens=512)
            reasons.append("latency HIGH -> reasoning low · 출력 512")
        elif state.get("task_complexity") == "HIGH" and state.get("latency_pressure") != "HIGH":
            p["reasoning"] = "high"
            reasons.append("task_complexity HIGH -> reasoning high")
        return {"version": self.version, "plan": p, "reasons": reasons or ["상태가 고정과 같은 계획을 고름"]}


# -- Provider Policy ---------------------------------------------------------------------------------------------
class ProviderPolicy:
    """인터페이스. select(state, request) -> {"version", "provider", "model", "reason"}.

    다음 단계(이번에 짓지 않음): 상태 · provider 별 텔레메트리(성공 · 지연 · 비용)로 고르는 정책. 그것을 켜기 전에
    같은 과업을 두 provider 에 돌린 평가(`ms.eval`)가 먼저다 -- 어느 쪽이 낫다고 가정하지 않는다.
    """
    version = "provider-interface"

    def select(self, state: dict, request: dict) -> dict:
        raise NotImplementedError


class ExplicitProvider(ProviderPolicy):
    """요청이 이름 댄 provider 를 쓴다. 없으면 기본. 상태를 보지 않는다."""
    version = "provider-explicit-1"

    def __init__(self, default: str, models: "dict | None" = None):
        self.default, self.models = default, dict(models or {})

    def select(self, state: dict, request: dict) -> dict:
        name = request.get("provider") or self.default
        return {"version": self.version, "provider": name, "model": request.get("model") or self.models.get(name),
                "reason": "요청이 명시" if request.get("provider") else "기본값", "requested": request.get("provider")}


CONTEXT_SELECTORS = {c.version: c for c in (FixedContext(), AdaptiveContext())}
PROMPT_SELECTORS = {c.version: c for c in (FixedPrompt(), AdaptivePrompt())}


def replay(policy_record: dict) -> dict:
    """결정 기록(ms/decision_record.py -- 상태 · 판본 · 입력)으로 계획을 다시 계산한다. 같으면 {"ok": True}."""
    st = policy_record["state"]
    out, ok = {}, True
    c = policy_record["context_policy"]
    sel = CONTEXT_SELECTORS.get(c["version"])
    if sel is None:
        return {"ok": False, "why": f"모르는 맥락 정책 판본 {c['version']}"}
    again = sel.plan(st, policy_record["inputs"]["base_context"])
    out["context"] = again["params"] == c["params"]
    p = policy_record["prompt_policy"]
    psel = PROMPT_SELECTORS.get(p["version"])
    if psel is None:
        return {"ok": False, "why": f"모르는 프롬프트 정책 판본 {p['version']}"}
    out["prompt"] = psel.plan(st)["plan"] == p["plan"]
    pv = policy_record["provider_policy"]
    if pv["version"] == ExplicitProvider.version:
        out["provider"] = pv["provider"] == (pv.get("requested") or policy_record["inputs"]["default_provider"])
    ok = all(out.values())
    return {"ok": ok, **out}
