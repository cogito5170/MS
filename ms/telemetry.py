"""Telemetry -- 관측이지 상태가 아니다 (원칙 1).

Telemetry 는 "어디서 · 무슨 신호가 · 언제 · 어떤 값을 냈다" 는 기록일 뿐이다. 뜻이 없다.
`85` 가 섭씨인지 화씨인지, 어느 개체의 어느 속성을 추정하는지, 믿을 만한지는 Model 이 정한다.
그래서 이 모듈에는 그래프를 건드리는 길이 없다 -- 상태가 되는 길은 `StateManager.ingest` 하나다.

도구의 결과도 Telemetry 로 되돌아온다(`source="tool:<이름>"`). 도구가 "끝냈다" 고 말해도 그것은 관측이다.

**id 도 여기서 매기지 않는다**(CMD-M26): 안 주면 None 이고, 받는 State Manager 가 자기 셈으로 `t<n>` 을 매긴다. 모듈 전역 셈이면
한 프로세스의 두 Runtime 이 서로의 id(따라서 DC provenance · 결정 id)를 바꾼다(BD-104 의 3).

**이 모듈은 시계를 읽지 않는다**(PC-12 · BV-09). `ts` 를 안 주면 None 이고, 받는 State Manager 가 **주입받은 시계**로 찍는다.
시계는 하나다 -- Runtime(또는 CLI)이 State Manager 에 준 것.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Telemetry:
    source: str            # 누가 냈나 -- 센서 · 에이전트 · tool:<이름>
    entity: str            # 어느 개체에 대한 관측이라고 **주장하는가** (그 개체가 있는지는 State Manager 가 본다)
    signal: str            # 신호 이름 -- 속성 이름이 아니다. 무슨 속성인지는 Model 의 binding 이 정한다
    value: object
    ts: "float | None" = None    # 없으면 받는 State Manager 가 자기(주입받은) 시계로 찍는다
    meta: dict = field(default_factory=dict)
    id: "str | None" = None      # 없으면 받는 State Manager 가 매긴다(그 관리자의 셈)

    @classmethod
    def from_dict(cls, d: dict) -> "Telemetry":
        kw = {k: d[k] for k in ("source", "entity", "signal", "value") if k in d}
        if "ts" in d:
            kw["ts"] = float(d["ts"])
        if "meta" in d:
            kw["meta"] = dict(d["meta"])
        if "id" in d:
            kw["id"] = str(d["id"])
        return cls(**kw)
