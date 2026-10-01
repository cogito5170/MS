"""술어 -- `[속성, 연산, 값]`. 모형의 파생 상태 · 질의의 거르개 · 도구의 사전조건이 같은 것을 쓴다.

`eval` 은 없다. 연산은 아래 표가 전부다.

값 자리에 다른 속성을 걸 수 있다: `["input_tokens", ">=", {"prop": "token_budget", "mul": 0.9}]` -- 모형이 비율로
뜻을 정할 때 쓴다(예산의 90% 를 넘으면 HIGH). 걸린 속성이 없으면 거짓이다.

속성이 없으면 거짓이다(`exists` 는 그것을 묻는 연산). 비교할 수 없는 값(문자열 < 수)도 거짓이다 --
모르는 것을 참으로 세지 않는다.
"""
from __future__ import annotations

OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def check(pred) -> list:
    """모양이 틀린 술어를 미리 잡는다. 문제 목록(비었으면 성하다)."""
    if not isinstance(pred, (list, tuple)) or len(pred) not in (2, 3):
        return [f"술어는 [속성, 연산, 값] 이어야 한다: {pred!r}"]
    if len(pred) == 2:
        return [] if pred[1] in ("exists", "missing") else [f"값 없는 연산은 exists · missing 뿐: {pred!r}"]
    if pred[1] not in OPS:
        return [f"모르는 연산 {pred[1]!r} (쓸 수 있는 것: {', '.join(OPS)} · exists · missing)"]
    if pred[1] in ("in", "not_in") and not isinstance(pred[2], (list, tuple)):
        return [f"{pred[1]} 의 값은 목록이어야 한다: {pred!r}"]
    if isinstance(pred[2], dict):
        if set(pred[2]) - {"prop", "mul"} or not isinstance(pred[2].get("prop"), str):
            return [f"속성 참조는 {{\"prop\": 이름, \"mul\": 수}} 꼴이어야 한다: {pred!r}"]
        if pred[1] not in ("<", "<=", ">", ">=", "==", "!="):
            return [f"속성 참조는 비교 연산에만: {pred!r}"]
    return []


def _rhs(v, values):
    if isinstance(v, dict) and "prop" in v:
        ref = values.get(v["prop"])
        if ref is None or isinstance(ref, bool) or not isinstance(ref, (int, float)):
            return None
        return ref * v.get("mul", 1)
    return v


def holds(pred, values: dict) -> bool:
    prop, op = pred[0], pred[1]
    if op == "exists":
        return values.get(prop) is not None
    if op == "missing":
        return values.get(prop) is None
    if values.get(prop) is None:
        return False
    rhs = _rhs(pred[2], values)
    if rhs is None:
        return False
    try:
        return bool(OPS[op](values[prop], rhs))
    except TypeError:
        return False


def all_hold(preds, values: dict) -> bool:
    return all(holds(p, values) for p in preds)


def props_of(preds) -> set:
    out = set()
    for p in preds:
        out.add(p[0])
        if len(p) > 2 and isinstance(p[2], dict) and "prop" in p[2]:
            out.add(p[2]["prop"])
    return out
