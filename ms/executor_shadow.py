"""실행기를 DC 길에 **shadow** 로 붙인다 (CMD-M20 · BD-108 · BD-109 · action docs/EXECUTOR.md §3 의 3 번). 실행은 지금 길 그대로다.

action 의 실행기(`action.executor.execute`)와 명세(`action.spec.ActionModel`)를 **선택 의존**으로 쓴다. 명령 재료는 guard 의
`command_material` 이다. 셋 중 하나라도 없으면 부르지 않는다(지금과 같다).

    Dispatch(registry)                                          Runtime 하나에 하나. ActionModel · 처리기를 한 번 짓는다
    .shadow(intent, guard_result, decision_ref, issued_at_ms)   -> 원장에 적을 dict. 도구 호출 **바로 앞**에서 부른다

- ActionModel: ToolRegistry 의 도구(retrieve 빼고 -- CR 안의 일이다)를 `ActionSpec.from_tool` 로 읽는다. 판본은 도구 정의의
  내용 해시(`ms-tools-<16>`)라 정의가 바뀌면 판본도 바뀐다.
- 처리기: 도구마다 `ms_handler(tool.run)`. shadow 에서는 부르지 않는다 -- 붙었는지만 실행기가 본다.
- ActionCommand = guard `command_material(GuardResult, ActionIntent)` + `decision_ref`(실행 직전에 지은 결정 기록의 id, PC19 G1) +
  `issued_at`(ALLOW 시각, **ms**, PC19 G4). Guard 가 ALLOW · SAFE_ACTION 이 아니면 명령 재료가 없다 -- 명령 없이 그 까닭만 남긴다.
- 실행기는 `mode="shadow"` 다: 처리기를 부르지 않고 L0 에도 적지 않는다. `would_dispatch` 만 돌려준다.
- 어떤 오류도 밖으로 나가지 않는다(shadow). 까닭은 `error` 칸에 남는다.
"""
from __future__ import annotations

import importlib

from .tools import RETRIEVE


def _mods():
    try:
        ex = importlib.import_module("action.executor")
        spec = importlib.import_module("action.spec")
        cmd = importlib.import_module("guard.command")
        gforms = importlib.import_module("guard.forms")
    except ImportError:
        return None
    return ex, spec, cmd, gforms


def available() -> bool:
    return _mods() is not None


class Dispatch:
    def __init__(self, registry):
        self.ex, self.spec, self.cmd, self.gforms = _mods()
        from action.canonical import digest
        tools = [{"name": t.name, "target_model": t.target_model, "params": t.params,
                  "preconditions": [list(p) for p in t.preconditions], "risk": t.risk, "description": t.description}
                 for t in registry.tools.values() if t.name != RETRIEVE]
        self.model, self.model_error = None, None
        try:
            version = f"ms-tools-{digest(sorted(tools, key=lambda d: d['name']))}"
            self.model = self.spec.ActionModel(version, tuple(self.spec.ActionSpec.from_tool(d, version) for d in tools))
        except Exception as e:                       # 명세를 못 읽으면 실행기 shadow 는 까닭만 남긴다
            self.model_error = f"{type(e).__name__}: {e}"
        self.handlers = {t.name: self.ex.ms_handler(t.run) for t in registry.tools.values() if t.name != RETRIEVE}

    def shadow(self, intent, guard_result: "dict | None", decision_ref: str, issued_at_ms: float) -> dict:
        out = {"model": self.model.version if self.model else None, "command": None, "execution": None}
        try:
            if self.model is None:
                return dict(out, error=f"ActionModel: {self.model_error}")
            if intent is None or isinstance(intent, list):
                return dict(out, error="의도가 없다")
            if guard_result is None:
                return dict(out, error="Guard 결과가 없다")
            res = self.gforms.GuardResult.from_dict(guard_result)
            if res.verdict not in (self.gforms.ALLOW, self.gforms.SAFE_ACTION):
                return dict(out, error=f"Guard {res.verdict}({res.rule}) -- 명령 재료가 없다")
            command = self.cmd.build_command(self.cmd.command_material(res, intent), decision_ref, issued_at_ms)
            ex = self.ex.execute(command, self.model, self.handlers, mode=self.ex.SHADOW)
            return dict(out, command=command.to_dict(), execution=ex.to_dict())
        except Exception as e:
            return dict(out, error=f"{type(e).__name__}: {e}")
