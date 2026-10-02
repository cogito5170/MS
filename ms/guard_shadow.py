"""Guard 를 Arbiter 옆에서 **shadow** 로 부른다 (CMD-M17 · BD-102 · BD-103 · guard F4). 실행은 Arbiter 가 정한다.

Guard(cogito5170/guard, 계약 `guard-result/1`)는 **선택 의존**이다. 없으면 부르지 않고 지금과 같다.
Guard 는 action 계약(`action-contract/1`)을 import 한다. 그래서 의도(`ms/intent.py`)가 있을 때만 -- 곧 DC 배선일 때만 -- 부른다.

    material(reader)                          -> (DC 문맥 데이터, 목적 명세 데이터) | None   상태를 읽은 **직후**에 붙잡는다
    Shadow(registry, grants).check(it, mat, ctx, m) -> GuardResult dict                판마다, Arbiter 판정 **직후**(실행 전)

입력(guard 의 들어오는 꼴)
- DCView    `guard.dcview_from_dc(DC ctx.to_dict(), asdict(목적 명세), offers=, seen=)`. offers · seen 은 그 판의 CR 맥락이
            내놓은 것 · 본 것이다(DC 에는 겨냥 있는 행동과 본 판이 없다). digest 를 다시 계산하므로 고친 기록은 거절된다.
- StateView 판정 직전의 지금 상태(State Manager). 실체마다 판 · 속성 값 · 낡음(`is_stale`) · 나이. `allowed` 는 Guard 가 ALLOW 한
            되풀이 열쇠(`guard.repeat_key`) -- Guard 는 순수 함수라 기억을 여기(Runtime 하나에 하나)가 든다. Arbiter 의 A8 기억과 같은 넓이다.
- GuardModel ToolRegistry 의 ToolSpec(이름 · 대상 모형 · 인자 · 사전조건 · 위험) · Runtime 의 grants.

어댑터 · 재료 · 꼴 오류는 모두 **DENY(E)** 로 기록한다(GuardResult 꼴 그대로). 예외는 밖으로 나가지 않는다 -- shadow 다.
"""
from __future__ import annotations

import dataclasses
import importlib

SCHEMA = "guard-result/1"


def _guard():
    try:
        g = importlib.import_module("guard")
        forms = importlib.import_module("guard.forms")
    except ImportError:
        return None
    return g if getattr(forms, "GUARD_SCHEMA", None) == SCHEMA and hasattr(g, "evaluate") else None


def available() -> bool:
    return _guard() is not None


def material(reader):
    """DC 결정 문맥 데이터와 목적 명세 데이터. 리더가 `dc_material()` 을 주면 그것을, DC MSStateReader(또는 `.inner` 로 그것을
    감싼 것)면 `last.to_dict()` · `asdict(builder.purposes[purpose])` 를. 못 구하면 None(→ Guard 는 DENY(E) 로 기록한다).
    상태를 읽은 직후에 불러야 한다 -- `last` 는 다음 읽기에서 바뀐다."""
    if reader is None:
        return None
    if hasattr(reader, "dc_material"):
        return reader.dc_material()
    inner = getattr(reader, "inner", reader)
    last, b, name = getattr(inner, "last", None), getattr(inner, "b", None), getattr(inner, "purpose", None)
    if last is None or b is None or name is None or name not in getattr(b, "purposes", {}):
        return None
    return last.to_dict(), dataclasses.asdict(b.purposes[name])


class Shadow:
    """Runtime 하나에 하나. A8 기억(`allowed`)을 든다."""

    def __init__(self, registry, grants=(), mode: str = "shadow"):
        self.g = _guard()
        self.reg, self.grants, self.mode = registry, frozenset(grants), mode     # mode: GuardResult 의 칸(판정과 무관, G6)
        self.allowed: set = set()

    def _model(self):
        g = self.g
        specs = {t.name: g.ActionSpec(t.name, t.target_model, dict(t.params), tuple(t.preconditions), t.risk)
                 for t in self.reg.tools.values()}
        return g.GuardModel(specs, self.grants)

    def _state(self, m):
        g = self.g
        ents = {nid: g.Entity(n.model, n.version, {p: g.Prop(v.value, m.is_stale(nid, p), m.age(nid, p))
                                                   for p, v in n.props.items()})
                for nid, n in m.graph.nodes.items()}
        return g.StateView(ents, frozenset(self.allowed))

    def _error(self, it, why: str) -> dict:
        g = self.g
        return g.GuardResult(it.id, g.DENY, self.mode, "E", (), [f"[E] {why}"], None).to_dict()

    def check(self, it, mat, ctx, m) -> dict:
        """의도 하나 -> GuardResult dict. ctx 는 그 판의 CR 맥락, m 은 지금의 State Manager."""
        g = self.g
        try:
            if mat is None:
                return self._error(it, "DC 결정 문맥 재료가 없다(리더가 DC MSStateReader 가 아니다)")
            record, purpose = mat
            dc = g.dcview_from_dc(record, purpose, offers={o["tool"]: list(o["targets"]) for o in ctx.offers},
                                  seen=dict(ctx.seen))
            state = self._state(m)
            model = self._model()
        except Exception as e:                           # 어댑터 오류 = DENY(E)
            return self._error(it, f"어댑터: {type(e).__name__}: {e}")
        try:
            _, res = g.evaluate(it, dc, state, model, mode=self.mode)   # 예외는 Guard 안에서 DENY(E). 그 밖의 것도 여기서 막는다
            if res.verdict == g.ALLOW:
                node = m.graph.nodes.get(it.target)
                self.allowed.add(g.repeat_key(it, node.version if node is not None else -1))
            return res.to_dict()
        except Exception as e:
            return self._error(it, f"Guard 호출: {type(e).__name__}: {e}")
