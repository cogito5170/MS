"""Telemetry -- 관측이지 상태가 아니다 (원칙 1).

Telemetry 는 "어디서 · 무슨 신호가 · 언제 · 어떤 값을 냈다" 는 기록일 뿐이다. 뜻이 없다.
`85` 가 섭씨인지 화씨인지, 어느 개체의 어느 속성을 추정하는지, 믿을 만한지는 Model 이 정한다.
그래서 이 모듈에는 그래프를 건드리는 길이 없다 -- 상태가 되는 길은 `StateManager.ingest` 하나다.

도구의 결과도 Telemetry 로 되돌아온다(`source="tool:<이름>"`). 도구가 "끝냈다" 고 말해도 그것은 관측이다.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

_ids = itertools.count(1)


@dataclass(frozen=True)
class Telemetry:
    source: str            # 누가 냈나 -- 센서 · 에이전트 · tool:<이름>
    entity: str            # 어느 개체에 대한 관측이라고 **주장하는가** (그 개체가 있는지는 State Manager 가 본다)
    signal: str            # 신호 이름 -- 속성 이름이 아니다. 무슨 속성인지는 Model 의 binding 이 정한다
    value: object
    ts: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"t{next(_ids)}")

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
