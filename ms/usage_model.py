"""AI 사용의 모형 -- 정규 텔레메트리를 **상태**로 해석한다. 기존 Model · Relationship 구조를 그대로 쓴다.

개체:
    Session   (id "session:<이름>")  한 사용 흐름. 여기서 아래 여덟 상태가 나온다
    Provider  (id "provider:<이름>") 세션이 쓴 provider. 관계 `uses`(Session -> Provider, N:N)

Session 의 속성은 전부 **측정 창(measurement)** 이다 -- 그래프(→ 질의 → LLM)에 안 들어간다. 그래프에 사는 것은 파생 상태뿐이다:

    상태                    해석(모형이 정한 뜻)
    token_budget_pressure   마지막 실행의 input_tokens 가 token_budget 의 0.9 배 이상 HIGH · 0.6 배 이상 MEDIUM · 아니면 LOW
    context_pressure        마지막 실행의 context_tokens 가 context_budget 의 0.9 · 0.6 배
    latency_pressure        최근 5 실행 total_ms 평균이 latency_budget_ms 의 1.0 · 0.7 배
    task_complexity         질의에 걸린 행(matched_rows) 40 · 10 이상, 또는 최근 5 실행 LLM 호출 평균 3 · 2 이상
    answer_reliability      (실행 3 개 이상일 때만) 최근 5 실행에서 못 읽은 제안 비율 >= 0.2 이거나 DENY 가 LLM 호출의 절반 이상 LOW ·
                            못 읽은 제안 <= 0.05 이고 DENY 가 호출의 0.1 이하 HIGH · 아니면 MEDIUM.
                            LOW 는 그 사건이 창 안에 **2 번 이상**일 때만 -- 한 번으로는 MEDIUM 까지
    correction_rate         (피드백 3 개 이상일 때만) 최근 10 피드백 중 사용자 고침 비율 0.2 · 0.05 이상.
                            HIGH 는 고침이 **2 번 이상**일 때만
    retry_pressure          최근 5 실행 재시도 평균 1 · 0.3 이상
    tool_churn              최근 5 실행에서 진전 없는 판(DENY · RETRIEVE 로 끝난 판) 평균 2 · 1 이상

입력이 하나라도 없으면 상태는 **모름(없음)** 이다 -- 기본값으로 메우지 않는다. 정책은 모름을 "고정 정책대로" 로 읽는다.

**문턱은 잰 것이 아니다.** 손으로 둔 값이고, 바꾸면 `MODEL_VERSION` 을 올린다(정책 재현이 그것을 본다).
"""
from __future__ import annotations

MODEL_VERSION = "usage-model-3"
# usage-model-2 -> 3 (2026-10-02): 뜻은 그대로, 신호 이름만 interaction.walp_denies -> interaction.arbiter_denies
# usage-model-1 -> 2 (2026-10-01): 품질 상태(answer_reliability LOW · correction_rate HIGH)는 **사건 하나로 정하지 않는다.**
#   재측정에서 세션 셋째 실행의 못 읽은 답 하나(1/3 = 0.33 >= 0.2)로 LOW 가 되어 품질 우선으로 뒤집혔다.
#   창 5 · 문턱 0.2 면 창 안의 실패 **한 번**이 곧 LOW 다 -- 표본 수만 늘려서는 안 고쳐진다(1/3 도 0.33).
#   그래서: 표본 >= MIN_SAMPLES 이고, 그 사건이 창 안에 MIN_EVENTS 번 이상이고, 비율이 문턱 이상일 때만.
MIN_SAMPLES = 3
MIN_EVENTS = 2
STATES = ("token_budget_pressure", "context_pressure", "latency_pressure", "task_complexity",
          "answer_reliability", "correction_rate", "retry_pressure", "tool_churn")


def _ev(type_="number", window=1, agg="last", **kw):
    return dict(type=type_, role="measurement", window=window, agg=agg, min=0, **kw) if type_ in ("number", "integer") \
        else dict(type=type_, role="measurement", window=window, agg=agg, **kw)


def _three(prop, ref, hi, mid, names=("HIGH", "MEDIUM", "LOW")):
    r = (lambda k: {"prop": ref, "mul": k}) if ref else (lambda k: k)
    return {"cases": [{"when": [[prop, ">=", r(hi)]], "value": names[0]},
                      {"when": [[prop, ">=", r(mid)]], "value": names[1]},
                      {"when": [[prop, ">=", 0]], "value": names[2]}], "default": None}


SESSION = {
    "name": "Session",
    "description": "LLM 사용 흐름 하나. 속성은 전부 측정 창, 상태는 파생만",
    "properties": {
        # 마지막 실행 (window 1)
        "input_tokens": _ev("integer"), "context_tokens": _ev("integer"), "matched_rows": _ev("integer"),
        # 최근 창
        "total_ms": _ev("number", 5, "mean"), "llm_calls": _ev("number", 5, "mean"), "retries": _ev("number", 5, "mean"),
        "non_progress_rounds": _ev("number", 5, "mean"),
        "proposal_invalid": _ev("number", 5, "mean"), "arbiter_denies": _ev("number", 5, "mean"),
        "user_correction": _ev("bool", 10, "mean"),
        # 상태를 정하는 데 안 쓰지만 모형이 아는 것(그래야 격리함이 아니라 측정 창으로 간다)
        "output_tokens": _ev("integer"), "cached_input_tokens": _ev("integer"), "retrieved_tokens": _ev("integer"),
        "total_tokens": _ev("integer"), "ttft_ms": _ev("number"), "inference_ms": _ev("number"),
        "tool_calls": _ev("number", 5, "mean"), "context_retrievals": _ev("number", 5, "mean"),
        "task_success": _ev("bool", 10, "mean"), "tool_success": _ev("bool", 10, "mean"),
        # 설정(예산) -- 사람이 정한 것도 '일어난 일' 로 들어온다(source=config)
        "token_budget": _ev("integer"), "context_budget": _ev("integer"), "latency_budget_ms": _ev("number"),
    },
    "bindings": [
        {"signal": "tokens.input_tokens", "property": "input_tokens"},
        {"signal": "tokens.context_tokens", "property": "context_tokens"},
        {"signal": "task.matched_rows", "property": "matched_rows"},
        {"signal": "latency.total_ms", "property": "total_ms"},
        {"signal": "interaction.llm_calls", "property": "llm_calls"},
        {"signal": "interaction.retries", "property": "retries"},
        {"signal": "interaction.non_progress_rounds", "property": "non_progress_rounds"},
        {"signal": "interaction.proposal_invalid", "property": "proposal_invalid"},
        {"signal": "interaction.arbiter_denies", "property": "arbiter_denies"},
        {"signal": "outcome.user_correction", "property": "user_correction"},
        {"signal": "tokens.output_tokens", "property": "output_tokens"},
        {"signal": "tokens.cached_input_tokens", "property": "cached_input_tokens"},
        {"signal": "tokens.retrieved_tokens", "property": "retrieved_tokens"},
        {"signal": "tokens.total_tokens", "property": "total_tokens"},
        {"signal": "latency.ttft_ms", "property": "ttft_ms"},
        {"signal": "latency.inference_ms", "property": "inference_ms"},
        {"signal": "interaction.tool_calls", "property": "tool_calls"},
        {"signal": "interaction.context_retrievals", "property": "context_retrievals"},
        {"signal": "outcome.task_success", "property": "task_success"},
        {"signal": "outcome.tool_success", "property": "tool_success"},
        {"signal": "config.token_budget", "property": "token_budget"},
        {"signal": "config.context_budget", "property": "context_budget"},
        {"signal": "config.latency_budget_ms", "property": "latency_budget_ms"},
    ],
    "derived": {
        "token_budget_pressure": _three("input_tokens", "token_budget", 0.9, 0.6),
        "context_pressure": _three("context_tokens", "context_budget", 0.9, 0.6),
        "latency_pressure": _three("total_ms", "latency_budget_ms", 1.0, 0.7),
        "task_complexity": {"cases": [
            {"when": [["matched_rows", ">=", 40], ["llm_calls", ">=", 0]], "value": "HIGH"},
            {"when": [["llm_calls", ">=", 3], ["matched_rows", ">=", 0]], "value": "HIGH"},
            {"when": [["matched_rows", ">=", 10], ["llm_calls", ">=", 0]], "value": "MEDIUM"},
            {"when": [["llm_calls", ">=", 2], ["matched_rows", ">=", 0]], "value": "MEDIUM"},
            {"when": [["matched_rows", ">=", 0], ["llm_calls", ">=", 0]], "value": "LOW"}], "default": None},
        "answer_reliability": {"cases": [
            {"when": [["proposal_invalid", ">=", 0.2], ["proposal_invalid__sum", ">=", MIN_EVENTS],
                      ["proposal_invalid__n", ">=", MIN_SAMPLES], ["arbiter_denies", ">=", 0], ["llm_calls", ">", 0]],
             "value": "LOW"},
            {"when": [["arbiter_denies", ">=", {"prop": "llm_calls", "mul": 0.5}], ["arbiter_denies__sum", ">=", MIN_EVENTS],
                      ["proposal_invalid__n", ">=", MIN_SAMPLES], ["proposal_invalid", ">=", 0], ["llm_calls", ">", 0]],
             "value": "LOW"},
            {"when": [["proposal_invalid", "<=", 0.05], ["arbiter_denies", "<=", {"prop": "llm_calls", "mul": 0.1}],
                      ["proposal_invalid__n", ">=", MIN_SAMPLES], ["llm_calls", ">", 0]], "value": "HIGH"},
            {"when": [["proposal_invalid", ">=", 0], ["arbiter_denies", ">=", 0],
                      ["proposal_invalid__n", ">=", MIN_SAMPLES], ["llm_calls", ">", 0]], "value": "MEDIUM"}],
            "default": None},
        "correction_rate": {"cases": [
            {"when": [["user_correction", ">=", 0.2], ["user_correction__sum", ">=", MIN_EVENTS],
                      ["user_correction__n", ">=", MIN_SAMPLES]], "value": "HIGH"},
            {"when": [["user_correction", ">=", 0.05], ["user_correction__n", ">=", MIN_SAMPLES]], "value": "MEDIUM"},
            {"when": [["user_correction", ">=", 0], ["user_correction__n", ">=", MIN_SAMPLES]], "value": "LOW"}],
            "default": None},
        "retry_pressure": _three("retries", None, 1.0, 0.3),
        "tool_churn": _three("non_progress_rounds", None, 2.0, 1.0),
    },
}

PROVIDER = {"name": "Provider", "description": "추론 provider. 상태 없음 -- 관계의 끝점",
            "properties": {"name": {"type": "string"}}, "bindings": []}

USES = {"name": "uses", "source": "Session", "target": "Provider", "cardinality": "N:N"}


def install(manager):
    """모형 둘과 관계 하나를 이미 있는 State Manager 에 더한다(세계 모형과 나란히)."""
    if "Session" not in manager.models:
        manager.add_model(SESSION)
        manager.add_model(PROVIDER)
        manager.add_relationship(USES)


def session_id(name: str) -> str:
    return f"session:{name}"


def open_session(manager, name: str, budgets: dict, source="config"):
    install(manager)
    sid = session_id(name)
    manager.declare(sid, "Session")
    ts = manager.clock()
    for k in ("token_budget", "context_budget", "latency_budget_ms"):
        if budgets.get(k) is not None:
            manager.ingest({"source": source, "entity": sid, "signal": f"config.{k}", "value": budgets[k], "ts": ts})
    return sid


def link_provider(manager, sid: str, provider: str):
    pid = f"provider:{provider}"
    manager.declare(pid, "Provider")
    manager.relate("uses", sid, pid)
    return pid


def snapshot(manager, sid: str) -> dict:
    """정책이 보는 것 -- 세션의 **파생 상태**만(원 측정은 없다). 재현을 위해 실행 기록에 그대로 남는다."""
    node = manager.graph.nodes.get(sid)
    vals = {} if node is None else {k: v.value for k, v in node.props.items() if v.derived}
    return {"model_version": MODEL_VERSION, **{s: vals.get(s) for s in STATES}}
