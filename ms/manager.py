"""State Manager -- 텔레메트리가 상태가 되는 유일한 문.

    ingest(t) -> applied   모형의 binding 으로 해석 · 검증을 지나 그래프에 들어갔다(파생도 다시 계산)
                 unbound   개체가 없거나 그 개체의 모형이 이 신호를 모른다 -- 상태가 아니다
                 rejected  변환 · 타입 · 범위 검증에 졌다 -- 상태가 아니다
                 stale     이미 더 새로운 관측이 있다(늦게 온 것) -- 덮어쓰지 않는다

unbound · rejected 는 격리함(`quarantine`)에 마지막 몇 개만 남는다. 그래프에는 절대 안 들어간다.

모형이 `role: measurement` 로 적은 속성은 그래프가 아니라 측정 창(`measurements`, 최근 `window` 개)에 쌓이고, 파생 상태의 입력으로만
쓰인다(PC-04: 옛 이름 evidence. Evidence 는 근거 참조의 이름이라 바꿨다).
그래서 질의(→ LLM)로는 원 측정이 보이지 않는다 -- 보이는 것은 모형이 해석한 상태뿐이다.

**시계는 주입받는다**(PC-12 · BV-09). 기본 시계가 없다 -- 만드는 쪽(Runtime · CLI · 평가)이 정한다. 시각이 없는 관측은 받을 때 이 시계로 찍는다.
"""
from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from .graph import StateGraph, Value
from .model import MEASUREMENT, Model, ModelError, RelationshipSpec, aggregate
from .telemetry import Telemetry

APPLIED, UNBOUND, REJECTED, STALE = "applied", "unbound", "rejected", "stale"


@dataclass
class IngestResult:
    status: str
    telemetry: str
    reason: str = ""
    changes: dict = field(default_factory=dict)    # 속성 -> (전, 후)


class StateManager:
    def __init__(self, models=(), relationships=(), *, clock, quarantine_size: int = 100):
        self.models: dict = {}
        self.relationships: dict = {}
        self.graph = StateGraph()
        self.clock = clock
        self.counts: Counter = Counter()
        self.quarantine: deque = deque(maxlen=quarantine_size)
        self.measurements: dict = defaultdict(dict)   # 측정 창: 개체 -> 속성 -> deque[Value] (그래프 밖)
        for m in models:
            self.add_model(m)
        for r in relationships:
            self.add_relationship(r)

    # -- 구조 ---------------------------------------------------------------
    def add_model(self, m) -> Model:
        m = m if isinstance(m, Model) else Model.from_dict(m)
        self.models[m.name] = m
        return m

    def add_relationship(self, r) -> RelationshipSpec:
        r = r if isinstance(r, RelationshipSpec) else RelationshipSpec.from_dict(r)
        for side in (r.source, r.target):
            if side not in self.models:
                raise ModelError(f"관계 {r.name}: 모형 {side} 가 없다")
        self.relationships[r.name] = r
        return r

    def declare(self, nid: str, model: str):
        """개체를 세운다. 개체가 있다는 것은 구조적 사실이지 텔레메트리가 아니다."""
        if model not in self.models:
            raise ModelError(f"개체 {nid}: 모형 {model} 이 없다")
        if nid in self.graph.nodes:
            if self.graph.nodes[nid].model != model:
                raise ModelError(f"개체 {nid}: 이미 {self.graph.nodes[nid].model} 이다")
            return self.graph.nodes[nid]
        return self.graph.add_node(nid, model)

    def relate(self, rel: str, a: str, b: str):
        spec = self.relationships.get(rel)
        if spec is None:
            raise ModelError(f"모르는 관계 {rel}")
        for nid, want in ((a, spec.source), (b, spec.target)):
            if nid not in self.graph.nodes:
                raise ModelError(f"관계 {rel}: 개체 {nid} 가 없다")
            if self.graph.nodes[nid].model != want:
                raise ModelError(f"관계 {rel}: {nid} 는 {self.graph.nodes[nid].model} 인데 {want} 여야 한다")
        if spec.cardinality in ("1:N", "1:1") and [x for x in self.graph.inn(b, rel) if x != a]:
            raise ModelError(f"관계 {rel}({spec.cardinality}): {b} 에 이미 들어온 것이 있다")
        if spec.cardinality == "1:1" and [x for x in self.graph.out(a, rel) if x != b]:
            raise ModelError(f"관계 {rel}(1:1): {a} 에서 이미 나간 것이 있다")
        self.graph.add_edge(rel, a, b)

    # -- 텔레메트리 -> 상태 --------------------------------------------------
    def _refuse(self, status, t, reason) -> IngestResult:
        self.counts[status] += 1
        if status in (UNBOUND, REJECTED):
            self.quarantine.append({"status": status, "id": t.id, "entity": t.entity, "signal": t.signal,
                                    "reason": reason})
        return IngestResult(status, t.id, reason)

    def ingest(self, t) -> IngestResult:
        t = t if isinstance(t, Telemetry) else Telemetry.from_dict(t)
        if t.ts is None:
            t = dataclasses.replace(t, ts=self.clock())
        node = self.graph.nodes.get(t.entity)
        if node is None:
            return self._refuse(UNBOUND, t, f"개체 {t.entity} 가 없다")
        model = self.models[node.model]
        b = model.bindings.get(t.signal)
        if b is None:
            return self._refuse(UNBOUND, t, f"모형 {model.name} 은 신호 {t.signal} 를 모른다")
        try:
            raw = b.interpret(t.value)
        except Exception as e:     # 변환이 터지는 것도 '이 값은 상태가 못 된다' 이다
            return self._refuse(REJECTED, t, f"변환 실패: {e}")
        spec = model.properties[b.property]
        val, problem = spec.validate(raw)
        if problem:
            return self._refuse(REJECTED, t, problem)
        if spec.role == MEASUREMENT:
            win = self.measurements[node.id].get(b.property)
            if win is not None and win and win[-1].ts > t.ts:
                return self._refuse(STALE, t, f"{b.property} 는 이미 더 새로운 관측({win[-1].src})이 있다")
            if win is None:
                win = self.measurements[node.id][b.property] = deque(maxlen=int(spec.window))
            win.append(Value(val, t.ts, t.id))
            self.counts[APPLIED] += 1
            return IngestResult(APPLIED, t.id, "", self._derive(node.id))
        cur = node.props.get(b.property)
        if cur is not None and cur.ts > t.ts:
            return self._refuse(STALE, t, f"{b.property} 는 이미 더 새로운 관측({cur.src})이 있다")
        changes = {}
        self.graph.set_prop(node.id, b.property, Value(val, t.ts, t.id))
        if cur is None or cur.value != val:
            changes[b.property] = (None if cur is None else cur.value, val)
        changes.update(self._derive(node.id))
        self.counts[APPLIED] += 1
        return IngestResult(APPLIED, t.id, "", changes)

    def _derive(self, nid: str) -> dict:
        node = self.graph.nodes[nid]
        model = self.models[node.model]
        base = {k: v.value for k, v in node.props.items() if not v.derived}
        when = {k: v.ts for k, v in node.props.items() if not v.derived}
        for k, win in self.measurements.get(nid, {}).items():
            base[k] = aggregate(model.properties[k].agg, [v.value for v in win])
            when[k] = win[-1].ts
            base[f"{k}__n"], when[f"{k}__n"] = len(win), win[-1].ts
            base[f"{k}__sum"], when[f"{k}__sum"] = aggregate("sum", [v.value for v in win]), win[-1].ts
        changes = {}
        for name, d in model.derived.items():
            new = d.compute(base)
            old = node.props.get(name)
            if new is None:
                if old is not None:
                    self.graph.drop_prop(nid, name)
                    changes[name] = (old.value, None)
                continue
            ins = sorted(d.inputs)
            ts = min(when[p] for p in ins) if ins else self.clock()
            self.graph.set_prop(nid, name, Value(new, ts, "derived:" + ",".join(ins), derived=True))
            if old is None or old.value != new:
                changes[name] = (None if old is None else old.value, new)
        return changes

    # -- 신선도 -------------------------------------------------------------
    def age(self, nid: str, prop: str):
        v = self.graph.nodes[nid].props.get(prop)
        if v is None:
            win = self.measurements.get(nid, {}).get(prop)
            v = win[-1] if win else None
        return None if v is None else max(0.0, self.clock() - v.ts)

    @property
    def evidence(self) -> dict:
        """옛 이름 -- `measurements` 와 같은 것. **DC `dc/sources.py` 가 아직 이 이름을 읽어서** 그쪽이 바꿀 때까지만 둔다
        (PC-04, baseline 에 요청). MS 코드는 쓰지 않는다(시험이 붙든다)."""
        return self.measurements

    def measurement_value(self, nid: str, prop: str):
        """측정 창의 모은 값(시험 · 진단용). 맥락 쪽은 이것을 부르지 않는다."""
        win = self.measurements.get(nid, {}).get(prop)
        if not win:
            return None
        return aggregate(self.models[self.graph.nodes[nid].model].properties[prop].agg, [v.value for v in win])

    def is_stale(self, nid: str, prop: str) -> bool:
        """값이 없거나 ttl 을 넘겼으면 낡았다."""
        node = self.graph.nodes[nid]
        if node.props.get(prop) is None:
            return True
        ttl = self.models[node.model].ttl_of(prop)
        return ttl is not None and self.age(nid, prop) > ttl

    @classmethod
    def from_spec(cls, spec: dict, *, clock) -> "StateManager":
        m = cls(spec.get("models", ()), spec.get("relationships", ()), clock=clock)
        for e in spec.get("entities", ()):
            m.declare(e["id"], e["model"])
        for rel, a, b in spec.get("edges", ()):
            m.relate(rel, a, b)
        return m
