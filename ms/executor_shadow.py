"""실행기를 DC 길에 **shadow** 로 붙인다 (CMD-M20 · CMD-M21 · BD-108 · BD-111 · action docs/EXECUTOR.md §3 의 3 번). 실행은 지금 길 그대로다.

    Dispatch(registry).shadow(intent, arbiter_verdict, guard_result, decision_ref, issued_at_ms) -> 원장에 적을 dict

도구 호출 **바로 앞**에서 부른다. action(실행기 · 명세)은 MS 의 필수 의존이고, guard 는 선택이다(대조에만 쓴다).

- ActionModel: `registry.model`(도구 정의 → `ActionSpec.from_tool`, BD-111). 처리기: 도구마다 `ms_handler(tool.run)` --
  shadow 에서는 부르지 않는다. 붙었는지만 실행기가 본다.
- **명령 재료는 실행을 정하는 쪽(Arbiter)의 판정에서 짓는다**(BD-111, E3 전까지): Arbiter 가 ALLOW 한 의도에서 바로
  `intent_id · action · target · args`. Arbiter 가 ALLOW 가 아니면 명령이 없다.
  ActionCommand = 재료 + `decision_ref`(실행 직전에 지은 결정 기록의 id, PC19 G1) + `issued_at`(ALLOW 시각, **ms**, PC19 G4).
- Guard 결과는 옆에 기록만 한다. 둘 다 ALLOW 면 guard `command_material` 과 재료가 같은지 `material_vs_guard` 에 적는다
  (E3 에서 재료의 출처를 guard 로 바꿀 때 같은 명령이 나오는지의 근거).
- 실행기는 `mode="shadow"`: 처리기를 부르지 않고 L0 에도 적지 않는다. `would_dispatch` 만 돌려준다.
- 어떤 오류도 밖으로 나가지 않는다(shadow). 까닭은 `error` 칸에 남는다.
"""
from __future__ import annotations

import importlib

from action import executor as X
from action.forms import ActionCommand

from .tools import RETRIEVE

ALLOW = "ALLOW"


def available() -> bool:
    """action 은 필수 의존이라 늘 참이다. 시험이 `sys.modules["action.executor"] = None` 으로 끌 수 있게 그때마다 묻는다."""
    try:
        importlib.import_module("action.executor")
    except ImportError:
        return False
    return True


def _guard_command():
    try:
        return importlib.import_module("guard.command"), importlib.import_module("guard.forms")
    except ImportError:
        return None


def material(intent) -> dict:
    """Arbiter 가 ALLOW 한 의도 → 명령 재료(guard ALLOW 재료와 같은 칸)."""
    return {"intent_id": intent.id, "action": intent.action, "target": intent.target, "args": dict(intent.args)}


class Dispatch:
    def __init__(self, registry):
        self.reg = registry
        self.model, self.model_error = None, None
        try:
            self.model = registry.model
        except Exception as e:                       # 명세를 못 읽으면 까닭만 남긴다
            self.model_error = f"{type(e).__name__}: {e}"
        self.handlers = {t.name: X.ms_handler(t.run) for t in registry.tools.values() if t.name != RETRIEVE}

    def _vs_guard(self, intent, mat, guard_result) -> str:
        g = _guard_command()
        if g is None or guard_result is None:
            return "Guard 없음"
        cmd, forms = g
        res = forms.GuardResult.from_dict(guard_result)
        if res.verdict != forms.ALLOW:
            return f"Guard {res.verdict}({res.rule})"
        return "같음" if cmd.command_material(res, intent) == mat else "다름"

    def shadow(self, intent, arbiter_verdict: str, guard_result: "dict | None", decision_ref: str,
               issued_at_ms: float) -> dict:
        out = {"model": self.model.version if self.model else None, "command": None, "execution": None}
        try:
            if self.model is None:
                return dict(out, error=f"ActionModel: {self.model_error}")
            if intent is None or isinstance(intent, list):
                return dict(out, error="의도가 없다")
            if arbiter_verdict != ALLOW:
                return dict(out, error=f"Arbiter {arbiter_verdict} -- 명령이 없다")
            mat = material(intent)
            out["material_vs_guard"] = self._vs_guard(intent, mat, guard_result)
            command = ActionCommand(intent_id=mat["intent_id"], decision_ref=decision_ref, action=mat["action"],
                                    target=mat["target"], args=mat["args"], issued_at=issued_at_ms, deadline=None)
            ex = X.execute(command, self.model, self.handlers, mode=X.SHADOW)
            return dict(out, command=command.to_dict(), execution=ex.to_dict())
        except Exception as e:
            return dict(out, error=f"{type(e).__name__}: {e}")
