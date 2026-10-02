"""결정 기록 -- 정책이 **무엇을 보고**(state) **어느 판본으로** **무엇을 정했나**(맥락 · 프롬프트 · provider 계획, 중재 결정).

텔레메트리(`RunRecord` -- "무슨 일이 일어났나")와 떼어 둔다. 판단 · 결정 · 정책이 본 상태는 텔레메트리 기록에 들어가지 않는다
(L0 Telemetry 규칙, cogito5170/Telemetry docs/TELEMETRY.md 6 · 7 절). 둘은 `RunRecord.decision_ref == DecisionRecord.id` 로만 잇는다.

id 는 내용의 sha256 이다. 기록을 고치면 id 가 달라져 잇기가 끊긴다(`linked`). 재현은 `policy.replay(decision)` 그대로.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "ms-decision-record-1"
BODY = ("cr", "state", "context_policy", "prompt_policy", "provider_policy", "arbiter_decision", "inputs", "schema")


@dataclass(frozen=True)
class DecisionRecord:
    cr: str
    state: dict                 # 정책이 본 상태(파생 상태만 -- 원 측정은 없다)
    context_policy: dict
    prompt_policy: dict
    provider_policy: dict
    arbiter_decision: dict      # {"final": …, "all": [[verdict, rule], …]}
    inputs: dict                # 재현에 필요한 설정(base_context · default_provider)
    schema: str = SCHEMA_VERSION
    state_source: dict = field(default_factory=dict)   # 정책이 본 상태의 출처(state_reader 의 record -- 예: 결정 문맥 id · digest)

    @property
    def id(self) -> str:
        return decision_id(asdict(self))

    def to_dict(self) -> dict:
        return {**asdict(self), "id": self.id}


def decision_id(d: dict) -> str:
    body = {k: d[k] for k in BODY}
    if d.get("state_source"):          # 출처가 있을 때만 id 에 든다 -- 출처 없이 지은 앞의 결정 기록의 id 는 그대로다
        body["state_source"] = d["state_source"]
    return "dec-" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
                                   .encode("utf-8")).hexdigest()[:16]


def linked(run_record: dict, decision: dict) -> bool:
    """실행 기록이 이 결정에서 나왔고, 결정 기록이 고쳐지지 않았나."""
    return run_record.get("decision_ref") == decision.get("id") == decision_id(decision)
