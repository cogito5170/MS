"""Model -- 상태의 뜻을 정한다 (원칙 2).

같은 텔레메트리 `cpu_temp=185` 도 모형이 "이 신호는 화씨다" 라고 하면 85°C 가 되고, 모형이 그 신호를 모르면
상태가 되지 못한다. 모형이 정하는 것:

    properties   속성마다 타입 · 단위 · 범위 · 열거값 · ttl(초, 지나면 낡음)
    bindings     어느 신호가 어느 속성을 추정하나 + 변환(scale · offset · round · map)
    derived      속성에서 결정론적으로 나오는 상태(예: temp_c >= 90 -> critical). 입력이 하나라도 없으면 None(모름)

정의는 JSON 으로 적을 수 있다:

    {"name": "Server",
     "properties": {"temp_c": {"type": "number", "unit": "C", "min": -40, "max": 150, "ttl": 60}},
     "bindings": [{"signal": "cpu_temp_f", "property": "temp_c", "transform": [["offset", -32], ["scale", 0.5556]]}],
     "derived": {"status": {"cases": [{"when": [["temp_c", ">=", 90]], "value": "critical"}], "default": "normal"}}}
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import predicate

TYPES = ("number", "integer", "string", "bool", "enum")


class ModelError(ValueError):
    pass


@dataclass
class PropertySpec:
    name: str
    type: str = "number"
    unit: str = ""
    min: "float | None" = None
    max: "float | None" = None
    values: "list | None" = None      # enum
    ttl: "float | None" = None        # None 이면 낡지 않는다(구조적 사실 -- 역할 · 위치 같은 것)

    def validate(self, v):
        """(값, 문제). 문제가 있으면 그 값은 상태가 되지 못한다."""
        t = self.type
        if t == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None, f"{self.name}: 수가 아니다 ({v!r})"
            v = float(v)
            if v != v:
                return None, f"{self.name}: NaN"
        elif t == "integer":
            if isinstance(v, bool) or not isinstance(v, int):
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                else:
                    return None, f"{self.name}: 정수가 아니다 ({v!r})"
        elif t == "string":
            if not isinstance(v, str):
                return None, f"{self.name}: 문자열이 아니다 ({v!r})"
        elif t == "bool":
            if not isinstance(v, bool):
                return None, f"{self.name}: 참거짓이 아니다 ({v!r})"
        elif t == "enum":
            if v not in (self.values or []):
                return None, f"{self.name}: {v!r} 는 {self.values} 밖이다"
        if t in ("number", "integer"):
            if self.min is not None and v < self.min:
                return None, f"{self.name}: {v} < 최소 {self.min}{self.unit}"
            if self.max is not None and v > self.max:
                return None, f"{self.name}: {v} > 최대 {self.max}{self.unit}"
        return v, None


def _apply_transform(steps, v):
    for step in steps or []:
        op, arg = step[0], step[1] if len(step) > 1 else None
        if op == "scale":
            v = v * arg
        elif op == "offset":
            v = v + arg
        elif op == "round":
            v = round(v, int(arg or 0))
        elif op == "map":
            if v not in arg:
                raise ValueError(f"map 에 없는 값 {v!r}")
            v = arg[v]
        else:
            raise ValueError(f"모르는 변환 {op!r}")
    return v


@dataclass
class Binding:
    signal: str
    property: str
    transform: list = field(default_factory=list)

    def interpret(self, raw):
        return _apply_transform(self.transform, raw)


@dataclass
class Derivation:
    name: str
    cases: list                      # [{"when": [술어...], "value": v}, ...] 위에서부터 처음 맞는 것
    default: object = None

    @property
    def inputs(self) -> set:
        s = set()
        for c in self.cases:
            s |= predicate.props_of(c["when"])
        return s

    def compute(self, values: dict):
        if any(values.get(p) is None for p in self.inputs):
            return None                  # 모르는 입력이 있으면 모른다 -- default 로 메우지 않는다
        for c in self.cases:
            if predicate.all_hold(c["when"], values):
                return c["value"]
        return self.default


@dataclass
class Model:
    name: str
    properties: dict = field(default_factory=dict)     # 이름 -> PropertySpec
    bindings: dict = field(default_factory=dict)       # 신호 -> Binding
    derived: dict = field(default_factory=dict)        # 이름 -> Derivation
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Model":
        name = d["name"]
        props = {}
        for pname, ps in (d.get("properties") or {}).items():
            spec = PropertySpec(pname, **ps)
            if spec.type not in TYPES:
                raise ModelError(f"{name}.{pname}: 모르는 타입 {spec.type!r}")
            if spec.type == "enum" and not spec.values:
                raise ModelError(f"{name}.{pname}: enum 인데 values 가 없다")
            props[pname] = spec
        binds = {}
        for b in d.get("bindings") or []:
            bd = Binding(b["signal"], b["property"], list(b.get("transform") or []))
            if bd.property not in props:
                raise ModelError(f"{name}: 신호 {bd.signal} 가 없는 속성 {bd.property} 에 묶였다")
            if bd.signal in binds:
                raise ModelError(f"{name}: 신호 {bd.signal} 가 두 번 묶였다")
            binds[bd.signal] = bd
        der = {}
        for dname, dd in (d.get("derived") or {}).items():
            if dname in props:
                raise ModelError(f"{name}: 파생 {dname} 가 속성 이름과 겹친다")
            dv = Derivation(dname, list(dd.get("cases") or []), dd.get("default"))
            for c in dv.cases:
                for p in c["when"]:
                    bad = predicate.check(p)
                    if bad:
                        raise ModelError(f"{name}.{dname}: {bad[0]}")
                    if p[0] not in props:
                        raise ModelError(f"{name}.{dname}: 없는 속성 {p[0]} 를 본다")
            der[dname] = dv
        return cls(name, props, binds, der, d.get("description", ""))

    def ttl_of(self, prop: str):
        if prop in self.properties:
            return self.properties[prop].ttl
        if prop in self.derived:   # 파생의 ttl = 입력 중 가장 짧은 것
            ttls = [self.properties[p].ttl for p in self.derived[prop].inputs if self.properties[p].ttl is not None]
            return min(ttls) if ttls else None
        return None


@dataclass
class RelationshipSpec:
    """관계도 모형이 정한다. cardinality: 'N:N' · '1:N'(대상은 이 관계로 들어오는 것이 하나뿐) · '1:1'."""
    name: str
    source: str
    target: str
    cardinality: str = "N:N"

    @classmethod
    def from_dict(cls, d: dict) -> "RelationshipSpec":
        r = cls(d["name"], d["source"], d["target"], d.get("cardinality", "N:N"))
        if r.cardinality not in ("N:N", "1:N", "1:1"):
            raise ModelError(f"관계 {r.name}: 모르는 cardinality {r.cardinality!r}")
        return r
