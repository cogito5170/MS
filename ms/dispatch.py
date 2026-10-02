"""DC 길의 도구 실행을 실행기로 한다 (CMD-M22 · BD-108 (4) · BD-111 · action docs/EXECUTOR.md §3 의 4 번).

    Dispatch(registry).run(intent, arbiter_verdict, guard_result, decision_ref, issued_at_ms, recorder)
        -> (원장에 적을 dict, 관측 목록 | None)

도구 호출 자리(Pipeline 의 ALLOW 가지)에서 부른다. **한 실행은 한 사건이다**(BD-97 Q3): 이 길로 실행하면 L0 에는
`action.dispatch` / `action.result` 만 남고(`action_ref` = command_id, 인자는 서명으로), `tool.*` 은 없다.
snapshot 길은 이것을 부르지 않는다 -- 지금처럼 `tool.run` · `tool.*` 이다.

- ActionModel: `registry.model`(도구 정의 → ActionSpec, BD-111). 처리기: 도구마다 `ms_handler(tool.run)`.
- 명령 재료는 실행을 정하는 쪽(Arbiter)의 판정에서 짓는다(BD-111, E3 전까지): Arbiter 가 ALLOW 한 의도의
  `intent_id · action · target · args`. ActionCommand = 재료 + `decision_ref`(실행 직전에 지은 결정 기록의 id, PC19 G1) +
  `issued_at`(ALLOW 시각, **ms**, PC19 G4).
- Guard 결과는 옆에 기록만 한다(shadow). 둘 다 ALLOW 면 guard `command_material` 과 재료가 같은지 `material_vs_guard` 에 적는다.
- 관측: 처리기가 돌려준 관측을 그대로 돌려준다(런타임이 지금처럼 ingest 한다). 처리기가 던지면 지금과 같은 `tool_error`
  관측 하나(메시지까지 -- `Execution.raised`). L0 에는 예외 **종류 이름만** 간다(실행기 · Recorder).
- 명령을 지을 수 없거나(의도 없음 · Arbiter 가 ALLOW 아님 · 명세 오류) 실행기가 거절하면(UNKNOWN_ACTION · NO_HANDLER)
  관측 대신 None 을 돌려준다 -- Pipeline 은 지금 길로 실행한다(실행을 잃지 않는다). 그 까닭은 원장 줄의 `fallback` 에 남는다.
"""
from __future__ import annotations

import importlib

from action import executor as X
from action.forms import ActionCommand

from .pipeline import tool_error
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
        except Exception as e:                       # 명세를 못 읽으면 지금 길로(까닭은 남긴다)
            self.model_error = f"{type(e).__name__}: {e}"
        # 처리기는 부를 때 도구를 찾는다 -- 나중에 붙인 handler(`ToolRegistry.bind`)도 그대로 따른다
        self.handlers = {n: X.ms_handler(lambda target, args, n=n: registry.get(n).run(target, args))
                         for n in registry.tools if n != RETRIEVE}

    def _vs_guard(self, intent, mat, guard_result) -> str:
        g = _guard_command()
        if g is None or guard_result is None:
            return "Guard 없음"
        cmd, forms = g
        res = forms.GuardResult.from_dict(guard_result)
        if res.verdict != forms.ALLOW:
            return f"Guard {res.verdict}({res.rule})"
        return "같음" if cmd.command_material(res, intent) == mat else "다름"

    def command(self, intent, arbiter_verdict: str, decision_ref: str, issued_at_ms: float, guard_result=None,
                source: str = "arbiter"):
        """(ActionCommand | None, 못 지은 까닭 | None). source: 재료의 출처 -- "arbiter"(shadow, BD-111) · "guard"(enforce,
        E3: guard `command_material`, SAFE_ACTION 이면 갈아 끼운 행동)."""
        if self.model is None:
            return None, f"ActionModel: {self.model_error}"
        if intent is None or isinstance(intent, list):
            return None, "의도가 없다"
        if arbiter_verdict != ALLOW:
            return None, f"Arbiter {arbiter_verdict} -- 명령이 없다"
        if source == "guard":
            g = _guard_command()
            if g is None or guard_result is None:
                return None, "Guard 결과가 없다 -- enforce 는 명령을 짓지 않는다"
            cmd, forms = g
            res = forms.GuardResult.from_dict(guard_result)
            if res.verdict not in (forms.ALLOW, forms.SAFE_ACTION):
                return None, f"Guard {res.verdict}({res.rule}) -- 명령이 없다"
            mat = cmd.command_material(res, intent)
        else:
            mat = material(intent)
        return ActionCommand(intent_id=mat["intent_id"], decision_ref=decision_ref, action=mat["action"],
                             target=mat["target"], args=mat["args"], issued_at=issued_at_ms, deadline=None), None

    def run(self, intent, arbiter_verdict: str, guard_result: "dict | None", decision_ref: str, issued_at_ms: float,
            recorder, source: str = "arbiter") -> "tuple[dict, list | dict | None]":
        out = {"model": self.model.version if self.model else None, "command": None, "execution": None}
        try:
            command, why = self.command(intent, arbiter_verdict, decision_ref, issued_at_ms, guard_result, source)
        except Exception as e:
            command, why = None, f"{type(e).__name__}: {e}"
        if command is None:
            return dict(out, fallback=why), None
        out["material_vs_guard"] = self._vs_guard(intent, material(intent), guard_result)
        ex = X.execute(command, self.model, self.handlers, recorder=recorder, mode=X.EXECUTE)
        out.update(command=command.to_dict(), execution=ex.to_dict())
        if not ex.executed:                          # 실행기가 거절했다 -- 실행을 잃지 않게 지금 길로
            return dict(out, fallback=f"실행기 거절 {ex.refused}"), None
        obs = list(ex.observations)
        if ex.raised is not None:
            obs = [tool_error(command.target, ex.raised)]
        if (command.action, command.target) != (intent.action, intent.target):   # 갈아 끼운 행동으로 실행했다(SAFE_ACTION)
            return out, {"observations": obs, "action": command.action, "target": command.target}
        return out, obs
