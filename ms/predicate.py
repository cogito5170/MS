"""술어 -- `[속성, 연산, 값]`. 모형의 파생 상태 · 질의의 거르개 · 도구의 사전조건이 같은 것을 쓴다.

`eval` 은 없다. 연산은 표(`OPS`)가 전부다.

값 자리에 다른 속성을 걸 수 있다: `["input_tokens", ">=", {"prop": "token_budget", "mul": 0.9}]` -- 모형이 비율로
뜻을 정할 때 쓴다(예산의 90% 를 넘으면 HIGH). 걸린 속성이 없으면 거짓이다.

속성이 없으면 거짓이다(`exists` 는 그것을 묻는 연산). 비교할 수 없는 값(문자열 < 수)도 거짓이다 --
모르는 것을 참으로 세지 않는다.

**한 벌은 action 에 있다**(`action.predicate`, BD-108 · BD-111). 이 파일은 그것을 다시 내보내는 얇은 층이다.
다른 점 하나: `props_of` 는 집합이다(MS 는 `|=` 로 모은다 -- `model.py`). action 의 것은 처음 나온 순서의 목록이다.
"""
from __future__ import annotations

try:
    from action.predicate import OPS, UNARY, _rhs, all_hold, check, holds  # noqa: F401
    from action.predicate import props_of as _props_of
except ImportError as e:                      # 필수 의존(pyproject 에 sha 고정) -- 조용히 다른 벌로 가지 않는다
    raise ImportError(f"action 계약이 없다({e}) -- pip install -e . 로 의존을 깔거나 "
                      "PYTHONPATH 에 cogito5170/action 을 두어라") from e


def props_of(preds) -> set:
    return set(_props_of(preds))
