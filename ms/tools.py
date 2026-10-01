"""도구 -- 어느 모형의 개체에 무엇을 하는가, 언제 해도 되는가, 얼마나 위험한가.

    risk   read         상태를 안 바꾼다
           local        우리 쪽만 바꾼다(되돌릴 수 있다)
           external     바깥에 닿는다(메일 · API · 배포)
           irreversible 되돌릴 수 없다(지우기 · 재부팅 · 결제)

`external` · `irreversible` 은 중재자에 허가(grants)가 있어야 ALLOW 된다. 기본은 DENY.

도구는 그래프를 못 본다. 손잡이(handler)는 `(대상, 인자) -> [텔레메트리 dict, ...]` 이고, 돌려준 것은
**관측으로** State Manager 에 들어간다 -- 도구가 "됐다" 고 해도 상태는 모형이 정한다(원칙 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import predicate
from .model import PropertySpec

RISKS = ("read", "local", "external", "irreversible")
RETRIEVE = "retrieve"


@dataclass
class ToolSpec:
    name: str
    target_model: str                  # "*" 이면 아무 모형
    description: str = ""
    params: dict = field(default_factory=dict)          # 이름 -> {"type", "min", "max", "values", "required"}
    preconditions: list = field(default_factory=list)   # 대상 개체의 상태에 대한 술어
    risk: str = "local"
    handler: object = None
    effect: list = field(default_factory=list)          # handler 가 없을 때: 텔레메트리 틀(시뮬 · 예시용)

    def __post_init__(self):
        if self.risk not in RISKS:
            raise ValueError(f"도구 {self.name}: 모르는 위험 등급 {self.risk!r}")
        for p in self.preconditions:
            bad = predicate.check(p)
            if bad:
                raise ValueError(f"도구 {self.name}: {bad[0]}")

    @classmethod
    def from_dict(cls, d: dict) -> "ToolSpec":
        kw = {k: d[k] for k in ("name", "target_model", "description", "params", "preconditions", "risk", "effect")
              if k in d}
        return cls(**kw)

    def check_args(self, args) -> list:
        if not isinstance(args, dict):
            return ["args 가 객체가 아니다"]
        out = []
        for k in args:
            if k not in self.params:
                out.append(f"모르는 인자 {k}")
        for k, ps in self.params.items():
            if k not in args:
                if ps.get("required", True):
                    out.append(f"인자 {k} 가 없다")
                continue
            spec = PropertySpec(k, **{x: ps[x] for x in ("type", "unit", "min", "max", "values") if x in ps})
            _, problem = spec.validate(args[k])
            if problem:
                out.append(problem)
        return out

    def run(self, target: str, args: dict) -> list:
        if self.handler is not None:
            return list(self.handler(target, args) or [])
        out = []
        for e in self.effect:
            e = dict(e)
            e.setdefault("entity", target)
            if isinstance(e.get("value"), str) and e["value"].startswith("$"):
                e["value"] = args.get(e["value"][1:])
            out.append(e)
        return out

    def card(self) -> dict:
        """LLM 에 보이는 꼴 -- 손잡이 · 사전조건의 속은 안 보인다(사전조건은 중재자가 지금 상태로 다시 본다)."""
        return {"name": self.name, "target_model": self.target_model, "risk": self.risk,
                "description": self.description, "params": self.params}


class ToolRegistry:
    def __init__(self, tools=()):
        self.tools: dict = {}
        self.add(ToolSpec(RETRIEVE, "*", "맥락에서 줄여 둔 것(handle)을 다시 꺼낸다. args: {\"handle\": \"h1\"}",
                          params={"handle": {"type": "string"}}, risk="read"))
        for t in tools:
            self.add(t)

    def add(self, t) -> ToolSpec:
        t = t if isinstance(t, ToolSpec) else ToolSpec.from_dict(t)
        self.tools[t.name] = t
        return t

    def get(self, name):
        return self.tools.get(name)

    def bind(self, name: str, handler):
        self.tools[name].handler = handler
