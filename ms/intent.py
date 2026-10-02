"""ActionIntent 를 따로 지어 기록한다 -- **shadow**(CMD-M15 · BD-97 · PC-19). 동작은 바꾸지 않는다.

Action 저장소(cogito5170/action)의 꼴 `action-contract/1` 을 **선택 의존**으로 쓴다(L0 Telemetry 와 같은 방식). 없으면 의도를 내지 않는다.

    intents(state_source, rounds) -> [{"round": n, "intent": ActionIntent dict} | {"round": n, "contract_error": [...]}]

- **DC 배선일 때만** 낸다. 조건은 `state_source` 가 state_reader 이고 결정 문맥 id 가 있는 것이고, 그때 dc_id = state_source["id"] 다.
  기본 길(`usage_model.snapshot`)에는 DC id 가 없어서 내지 않는다.
- policy = "ms-cr@<CR 판본>".
- LLM 제안(author_kind "llm")은 판마다 하나다. 의도가 아닌 셋이 있다:
  - error 가 있는 제안(A0)
  - "none"(NOOP)
  - "retrieve"(CR 안의 일이고 실행기가 없다)

  DENY 된 제안도 의도다. Guard · Validate 의 입력이기 때문이다(BD-100). 실행된 것은 마지막 판의 ALLOW 다.
  used_keys 는 결정 문맥이 준 질의의 이름에 `query:` 를 붙인 것이다(LLM 이 STATE 로 본 것, BD-100). 상태 키 "<역할>.<상태>" 와
  꼴을 가른다. 이것은 상한이다 -- LLM 이 실제로 무엇을 썼는지는 모른다.
- **기본 결정(BD-76 의 KEEP)은 의도가 아니다**(BD-100). CR 안의 맥락 결정(목적 context_runtime)은 실행기로 갈 행동이 아니고,
  의도로 남기면 실행기가 설 때 실행 대상으로 섞인다. 의도는 실행기로 갈 도구 행동뿐이다. 기본 결정은 결정 기록의 맥락 계획에 남는다.
- 의도는 결정 기록(DecisionRecord)에 넣지 않는다. 그래서 결정 id 는 그대로다. 잇기는 원장의 `decision_ref` 로만 한다.
- A8 되풀이 열쇠는 intent_id 가 아니라 `Proposal.key()` 그대로다(PC-19 G5). intent_id 에는 rationale 이 들어서, 까닭만 바꾼
  되풀이를 놓친다.
"""
from __future__ import annotations

import importlib

from .cr import VERSION as CR_VERSION
from .llm import NONE
from .tools import RETRIEVE

POLICY = f"ms-cr@{CR_VERSION}"
QUERY_KEY = "query:"      # 질의 결과의 키 접두(BD-100). 상태 키는 "<역할>.<상태>" 다


def _action():
    try:
        a = importlib.import_module("action")
    except ImportError:
        return None
    return a if str(getattr(a, "SPEC", "")).startswith("action-contract/") else None


def available() -> bool:
    return _action() is not None


def dc_id(state_source: "dict | None") -> "str | None":
    """결정 문맥 id. DC 배선(state_reader 가 id 를 준 것)일 때만 있다."""
    s = state_source or {}
    i = s.get("id")
    return i if s.get("kind") == "state_reader" and isinstance(i, str) and i else None


def intents(state_source: "dict | None", rounds: list) -> list:
    a, did = _action(), dc_id(state_source)
    if a is None or did is None:
        return []
    out = []

    def put(rnd, **kw):
        try:
            out.append({"round": rnd, "intent": a.ActionIntent(dc_id=did, policy=POLICY, **kw).to_dict()})
        except a.ContractError as e:          # shadow 다 -- 꼴이 안 맞아도 실행은 그대로 두고, 까닭만 남긴다
            out.append({"round": rnd, "contract_error": list(e.errors)})

    shown = [QUERY_KEY + q for q in (state_source or {}).get("queries", ())]
    for r in rounds:
        p = r.get("proposal")
        if not p or p.get("error") or p.get("tool") in (None, NONE, RETRIEVE):
            continue
        put(r["round"], action=p["tool"], target=p["target"], args=p["args"], rationale=p["rationale"],
            used_keys=shown, author_kind="llm")
    return out
