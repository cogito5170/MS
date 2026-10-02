"""② Sensor → Telemetry -- Sensor(llmsensor) 의 출력을 MS 의 텔레메트리 신호로만 편다.

MS 는 Sensor 를 import 하지 않는다. 계약은 `llmsensor.sense()` 출력의 **데이터 꼴**(dict 또는 JSON)이다.
선행조사: `paper/선행조사/센서배선.md`.

    받는 칸   telemetry(측정) · readings(관측) · residual(유도된 측정)
    안 받는 칸 fusion(Q -- 판독의 함수라 받으면 같은 증거를 두 번 센다, 증거를 묶어 뜻을 정하는 것은 MS 모형의 일)
              verdict(ACCEPT · RETRY … · 정책 제안 -- Sensor 가 이미 내린 **결정**. 받으면 Sensor 와 Policy 가 섞인다)
              -> 쓰지 않고 `refused` 에 **칸 이름만** 남긴다(내용은 안 남긴다)

**수만 받는다.** 판독의 `why` 와 날글 칸(명령 출력 꼬리 · 파일 경로 · 답의 값 · 명령줄)은 버린다 -- Sensor 판 d4b80b3 에서
outcome.detail.tail 은 stdout · stderr 의 마지막 400 자이고 비밀이 섞일 수 있다. 받는 것은 status(OK · SUSPECT · FAULT · UNKNOWN) ·
value(수) · 아래 화이트리스트의 수 칸(목록 칸은 길이만)뿐이다. 화이트리스트에 없는 칸은 버리고 `dropped` 에 이름만 적는다
(Sensor 가 칸을 더해도 날글이 조용히 들어오지 못한다).

신호 이름은 MS 의 것이다:

    sensor.tel.<칸>                     측정        예) sensor.tel.T · sensor.tel.E · sensor.tel.latency
    sensor.res.<칸>                     유도된 측정  예) sensor.res.R_sys · sensor.res.parts.T
    sensor.<센서>.status | .value        관측        예) sensor.execution.status = "FAULT"
    sensor.<센서>.<하위>.status | .value  하위 관측   예) sensor.behavior.loop.status
    sensor.<센서>[.<하위>].<칸>           수 칸       예) sensor.execution.unresolved (= 목록의 길이)

UNKNOWN 은 "UNKNOWN" 으로 들어간다(평가를 못 했다). OK 로 바꾸지 않는다. 값이 없으면(None) 신호를 내지 않는다.
이 신호들의 **뜻**은 ③(Telemetry → DC)의 모형이 정한다. 모형이 없으면 State Manager 는 그것들을 unbound 로 격리한다 -- 원칙 1 그대로.

`otel_events()` 는 판독을 OpenTelemetry GenAI `gen_ai.evaluation.result` 꼴로도 낸다(이름 · 라벨 · 값. 설명은 why 를 버렸으므로 없다).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

CONTRACT = "sensor-telemetry-1"          # 바꾸면 올린다
SENSOR_VERSION = "llmsensor@d4b80b3"     # 화이트리스트를 맞춘 Sensor 판
STATUSES = ("OK", "SUSPECT", "FAULT", "UNKNOWN")
ACCEPTED = ("telemetry", "readings", "residual")
REFUSED = ("fusion", "verdict")
IGNORED = ("cls",)                       # 과업 부류 이름 -- 문자열이라 신호로 안 낸다(필요하면 ③ 에서 따로 정한다)

TELEMETRY_FIELDS = ("T", "T_out", "turns", "L", "E", "R", "latency", "missing_results")
RESIDUAL_FIELDS = ("R_sys", "r_T", "z_T", "r_R", "r_E", "r_V")
RESIDUAL_NESTED = ("parts",)             # 수만 든 dict -- sensor.res.parts.<키>

# 판독(센서 · 하위 센서)마다 받을 detail 칸. "n" = 수(int · float), "b" = 참거짓, "len" = 목록이면 길이
DETAIL = {
    "execution": {"calls": "n", "counted": "n", "targets": "n", "errors": "n",
                  "unresolved": "len", "no_result": "len", "written_but_missing": "len"},
    "constraint": {"violations": "len", "unsupported": "len"},
    "outcome": {"returncode": "n", "seconds": "n"},                       # tail(출력 꼬리)은 버린다
    "consistency.claim": {"claim": "b", "verifications": "n", "last_ok": "b"},
    "consistency.numbers": {"numbers": "n", "ungrounded": "n"},
    "consistency.agreement": {"n": "n"},
    "behavior.loop": {"max_same_call_same_output": "n", "max_same_call": "n", "oscillation_cycles": "n"},
    "behavior.tokens": {"z": "n", "value": "n", "expected": "n"},
    "behavior.retry": {"R": "n", "z": "n", "value": "n", "expected": "n"},
    "behavior.latency": {"z": "n", "value": "n", "expected": "n"},
    "behavior.budget": {},
}
NESTED = {"consistency": ("claim", "numbers", "agreement"),
          "behavior": ("loop", "tokens", "retry", "latency", "budget")}
NESTED_SKIP = {"behavior": ("telemetry",)}      # behavior.detail.telemetry 는 sense()["telemetry"] 와 같다 -- 두 번 받지 않는다


@dataclass
class SensorTelemetry:
    signals: list = field(default_factory=list)    # [{"source","entity","signal","value","ts","meta"}]
    refused: list = field(default_factory=list)    # 받지 않은 칸 이름 (내용 없음)
    dropped: list = field(default_factory=list)    # 화이트리스트 밖이라 버린 칸 이름 (내용 없음)
    contract: str = CONTRACT

    def to_dict(self) -> dict:
        return {"contract": self.contract, "signals": self.signals, "refused": self.refused, "dropped": self.dropped}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _take(kind, v):
    if kind == "n":
        return _num(v)
    if kind == "b":
        return v if isinstance(v, bool) else None
    if kind == "len":
        if isinstance(v, (list, tuple, dict)):
            return len(v)
        return _num(v)
    return None


class _Out:
    def __init__(self, entity, ts, task_id):
        self.r = SensorTelemetry()
        self.entity, self.ts, self.meta = entity, ts, ({"task": task_id} if task_id else {})

    def put(self, signal, value):
        if value is None:
            return
        self.r.signals.append({"source": "sensor", "entity": self.entity, "signal": signal, "value": value,
                               "ts": self.ts, "meta": dict(self.meta)})

    def drop(self, name):
        if name not in self.r.dropped:
            self.r.dropped.append(name)


def _reading(o: _Out, path: str, rd: dict):
    """판독 하나(또는 하위 판독)를 편다. why 는 읽지도 않는다."""
    if not isinstance(rd, dict):
        o.drop(path)
        return
    st = rd.get("status")
    o.put(f"sensor.{path}.status", st if st in STATUSES else None)
    if st is not None and st not in STATUSES:
        o.drop(f"{path}.status")
    o.put(f"sensor.{path}.value", _num(rd.get("value")))
    det = rd.get("detail") or {}
    if not isinstance(det, dict):
        o.drop(f"{path}.detail")
        return
    allowed = DETAIL.get(path, {})
    nested = NESTED.get(path, ())
    skip = NESTED_SKIP.get(path, ())
    for k, v in det.items():
        if k in nested:
            _reading(o, f"{path}.{k}", v)
        elif k in skip:
            continue
        elif k in allowed:
            got = _take(allowed[k], v)
            if got is None and v is not None:
                o.drop(f"{path}.{k}")
            o.put(f"sensor.{path}.{k}", got)
        else:
            o.drop(f"{path}.{k}")


def to_telemetry(sense_output, entity: str, ts: float, task_id: "str | None" = None) -> SensorTelemetry:
    """`llmsensor.sense()` 의 출력(dict 또는 JSON 글) -> MS 텔레메트리 신호."""
    data = json.loads(sense_output) if isinstance(sense_output, (str, bytes)) else sense_output
    if not isinstance(data, dict):
        raise ValueError("sense() 출력은 dict 여야 한다")
    o = _Out(entity, ts, task_id)
    for k in data:
        if k in REFUSED:
            o.r.refused.append(k)
        elif k not in ACCEPTED and k not in IGNORED:
            o.drop(k)
    tel = data.get("telemetry") or {}
    for k, v in (tel.items() if isinstance(tel, dict) else ()):
        if k in TELEMETRY_FIELDS:
            got = _num(v)
            if got is None and v is not None:
                o.drop(f"tel.{k}")
            o.put(f"sensor.tel.{k}", got)
        else:
            o.drop(f"tel.{k}")
    res = data.get("residual") or {}
    for k, v in (res.items() if isinstance(res, dict) else ()):
        if k in RESIDUAL_FIELDS:
            o.put(f"sensor.res.{k}", _num(v))
        elif k in RESIDUAL_NESTED and isinstance(v, dict):
            for kk, vv in v.items():
                got = _num(vv)
                if got is None:
                    o.drop(f"res.{k}.{kk}")
                o.put(f"sensor.res.{k}.{kk}", got)
        else:
            o.drop(f"res.{k}")
    for rd in data.get("readings") or []:
        name = rd.get("sensor") if isinstance(rd, dict) else None
        if name not in ("execution", "constraint", "consistency", "behavior", "outcome"):
            o.drop(f"readings.{name}")
            continue
        _reading(o, name, rd)
    return o.r


def ingest(manager, sense_output, entity: str, ts: "float | None" = None, task_id: "str | None" = None) -> dict:
    """신호를 State Manager 에 넣는다. 받지 않은 칸은 manager 의 격리함에 **이름만** 남긴다.
    신호의 뜻을 정하는 모형(③)이 없으면 신호는 전부 unbound 로 격리된다 -- 그것이 맞다(Telemetry ≠ State)."""
    ts = manager.clock() if ts is None else ts
    st = to_telemetry(sense_output, entity, ts, task_id)
    results = [manager.ingest(s) for s in st.signals]
    for name in st.refused:
        manager.quarantine.append({"status": "refused", "id": None, "entity": entity, "signal": f"sensor:{name}",
                                   "reason": "Sensor 의 추정 · 결정 칸은 받지 않는다(텔레메트리만)"})
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"contract": st.contract, "signals": len(st.signals), "ingest": counts, "refused": st.refused,
            "dropped": st.dropped}


def otel_events(sense_output) -> list:
    """판독 -> OpenTelemetry GenAI `gen_ai.evaluation.result` 꼴(Development 안정도 -- 짝만 둔다). 설명(why)은 넣지 않는다."""
    st = to_telemetry(sense_output, entity="-", ts=0.0)
    vals, labels = {}, {}
    for s in st.signals:
        name = s["signal"]
        if name.endswith(".status"):
            labels[name[len("sensor."):-len(".status")]] = s["value"]
        elif name.endswith(".value"):
            vals[name[len("sensor."):-len(".value")]] = s["value"]
    out = []
    for path, label in labels.items():
        attrs = {"gen_ai.evaluation.name": path, "gen_ai.evaluation.score.label": label}
        if path in vals:
            attrs["gen_ai.evaluation.score.value"] = vals[path]
        out.append({"name": "gen_ai.evaluation.result", "attributes": attrs})
    return out
