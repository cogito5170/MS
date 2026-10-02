"""정규 실행 텔레메트리 -- 어느 provider 든 같은 꼴. "무슨 일이 일어났는가" 만 적는다.

    run          run_id · session_id · provider · model · timestamp
    tokens       input_tokens · output_tokens · cached_input_tokens · context_tokens · retrieved_tokens · total_tokens
    latency      ttft_ms · inference_ms · total_ms
    interaction  llm_calls · tool_calls · retries · context_retrievals · arbiter_denies · proposal_invalid · non_progress_rounds
    outcome      task_success · user_correction · tool_success
    decision_ref 이 실행을 낸 결정 기록의 id(DecisionRecord.id). 결정의 **내용**(정책 판본 · 계획 · 중재 결정 · 정책이 본 상태)은
                 여기 없다 -- ms/decision_record.py. 텔레메트리는 무슨 일이 일어났나만 적는다(ms-run-telemetry-3)
    cost         usd · source(provider 보고 | 가격표 | 없음)
    estimated    추정한 칸과 방법
    unsupported  요청했지만 provider 가 못 해서 안 보낸 옵션
    extensions   {"<provider>": [...]} -- canonical 에 없는 provider 고유 값. **상태로 안 간다**

칸의 뜻:
    input_tokens        OTel 규약 -- 캐시 읽기 포함 전체 입력, 실행 안의 LLM 호출 합
    context_tokens      입력 중 **최소 맥락** 몫. provider 가 따로 안 세므로 **추정**이다:
                        input_tokens × (맥락 글자 / 프롬프트 글자). `estimated` 에 방법이 적힌다
    retrieved_tokens    입력 중 RETRIEVE 로 다시 꺼낸 행의 몫. 같은 방식으로 추정
    ttft_ms             스트리밍일 때 첫 조각까지. 아니면 None
    inference_ms        provider 호출 시간의 합(네트워크 포함, 또는 provider 가 보고한 값)
    total_ms            요청 하나의 벽시계(맥락 짓기 · 중재 · 도구 포함)
    retries             첫 판 뒤에 **DENY 나 못 읽은 제안 때문에** 다시 돈 판 수(RETRIEVE 는 재시도가 아니다)
    task_success        실행 기록에서는 **늘 None** 이다(ms-run-telemetry-4, PC-13). 판정은 성공 기준을 가진 쪽(평가 하니스)이 하고
                        원장에 따로 `{"kind": "evaluation", "run_id", "task_success"}` 줄로 남는다 -- Runtime 도 LLM 도 판정하지 않는다
    user_correction     사람이 고쳤나. 피드백이 올 때 따로 들어온다. 아니면 None
    tool_success        도구를 돌렸으면 그 결과가 tool_error 없이 들어왔나. 안 돌렸으면 None

`to_signals()` 는 이 기록을 텔레메트리 신호로 편다(entity = 세션). extensions · unsupported · decision_ref 는 **신호로 안 편다** --
provider 고유 값이 상태의 뜻을 바꿀 길을 없앤다(원칙 9 · 10). 그것들은 원장(JSONL)에만 남는다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "ms-run-telemetry-4"     # 1 -> 2: walp_* 칸을 arbiter_* 로 · 2 -> 3 (2026-10-02): policy 칸을 결정 기록으로 떼고 decision_ref 만
                                         # 3 -> 4 (2026-10-02, PC-13): task_success 를 Runtime 이 채우지 않는다(판정은 원장의 evaluation 줄)

# OpenTelemetry GenAI 이름과의 짝(내보낼 때). 짝이 없는 칸은 MS 고유다
OTEL = {"run.provider": "gen_ai.provider.name", "run.model": "gen_ai.request.model",
        "tokens.input_tokens": "gen_ai.usage.input_tokens", "tokens.output_tokens": "gen_ai.usage.output_tokens",
        "latency.ttft_ms": "gen_ai.server.time_to_first_token"}


@dataclass
class RunRecord:
    run: dict
    tokens: dict
    latency: dict
    interaction: dict
    outcome: dict
    decision_ref: str
    cost: dict = field(default_factory=lambda: {"usd": None, "source": None})
    estimated: dict = field(default_factory=dict)
    unsupported: list = field(default_factory=list)
    extensions: dict = field(default_factory=dict)
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def to_signals(self, entity: str, ts: float, matched_rows: "int | None" = None) -> list:
        out = []

        def put(sig, v):
            if v is not None:
                out.append({"source": "ms:run", "entity": entity, "signal": sig, "value": v, "ts": ts,
                            "meta": {"run_id": self.run["run_id"]}})
        for k in ("input_tokens", "output_tokens", "cached_input_tokens", "context_tokens", "retrieved_tokens",
                  "total_tokens"):
            put(f"tokens.{k}", self.tokens.get(k))
        for k in ("ttft_ms", "inference_ms", "total_ms"):
            put(f"latency.{k}", self.latency.get(k))
        for k, v in self.interaction.items():
            put(f"interaction.{k}", v)
        for k in ("task_success", "tool_success"):
            put(f"outcome.{k}", self.outcome.get(k))
        put("task.matched_rows", matched_rows)
        return out

    def otel(self) -> dict:
        d = {"run": self.run, "tokens": self.tokens, "latency": self.latency}
        out = {}
        for path, name in OTEL.items():
            sec, key = path.split(".")
            v = d[sec].get(key)
            if v is not None:
                out[name] = v / 1000 if name.endswith("time_to_first_token") else v
        return out


def cost_of(model: str, tokens: dict, prices: "dict | None"):
    """가격표: {"<model>": {"input": $/MTok, "output": $/MTok, "cached_input": $/MTok}}. 없으면 None -- 지어내지 않는다."""
    if not prices or model not in prices:
        return None
    p, i, o = prices[model], tokens.get("input_tokens"), tokens.get("output_tokens")
    if i is None or o is None:
        return None
    c = tokens.get("cached_input_tokens") or 0
    cached_rate = p.get("cached_input", p["input"])
    return ((i - c) * p["input"] + c * cached_rate + o * p["output"]) / 1e6
