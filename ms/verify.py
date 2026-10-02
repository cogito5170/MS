"""Health VERIFY 를 부른다 (CMD-M23 · BD-99 · BD-101 · BD-108 §5). 판정은 기록만 한다 -- 결정에 되먹이지 않는다.

Health(cogito5170/health, 꼴 `verification-record/1`)는 **선택 의존**이다(guard 와 같다). 없으면 부르지 않고 지금과 같다.

    Verifier(registry, run_state=None)
    .after_execute(command, run, decision_ref, outcome, m, now_ms) -> VerificationRecord dict | None   실행(관측 ingest) 직후
    .close_windows(m, now_ms) -> [(decision_ref, VerificationRecord dict), ...]                  창이 닫힌 PENDING 을 다시

- 언제: (a) 실행기 `execute` 의 관측을 상태 관리자에 넣은 **직후**, (b) 창이 닫힐 때(`issued_at + window_ms`). 시계 · 일정은
  Runtime 의 것이다 -- Runtime 이 요청마다 먼저 `close_windows` 를 부르고, 바깥에서도 부를 수 있다(`Runtime.close_windows`).
  (a) 가 PENDING 이면 기다리는 목록에 넣고 (b) 에서 한 번 더 판정한다. 그 판정은 final 이다(창이 닫혔으므로).
- 사후조건 · 창: 행동 명세의 집(`registry.model` = ActionModel)의 `verify_args(spec)`.
  **사후조건이 없는 행동은 verify 를 부르지 않는다**(CMD-M23 덧붙임 · Health H3 F1): `verify_args` 는 그때 창이 None 이고
  `verify` 는 창 없이는 거절한다. 창을 지어내 NO_SPEC 을 받는 것은 값을 지어내는 일이다 -- 기록도 남기지 않는다.
- 읽기 `reads(실체, 상태)`:
  - MS 세계의 실체(`$target` 이 푸는 것) → **상태 관리자를 state-export `read` 꼴로 읽는 어댑터**(`state_reads`).
    값 · 유효성(OBSERVED · 파생이면 DERIVED) · 신선도(ttl 안이면 FRESH, 넘었으면 STALE) · 관측 시각(초 × 1000, unix_ms).
  - 그 밖의 실체(`$run.*` 이 푸는 agent · task · runtime) → `run_state.read`. `subjects` 는 `run_state.subjects(run)`.
    `run_state` 는 Sensor state-export 를 꽂을 이음매다 -- 없으면 `$run.*` 절은 UNRESOLVED_ENTITY 다.
- `outcome`(ActionOutcome)은 명령과 같은 것을 가리키는지 확인하는 데만 넘긴다 -- 판정에 쓰지 않는다(BD-99).
  `outcome_ref`(L0 action.result 사건 id)는 런타임이 모른다(Recorder 가 사건 id 를 돌려주지 않는다) -- None.
"""
from __future__ import annotations

import importlib

SCHEMA = "verification-record/1"
TIME_BASE = "unix_ms"


def _health():
    try:
        h = importlib.import_module("health")
    except ImportError:
        return None
    return h if getattr(h, "SCHEMA", None) == SCHEMA and hasattr(h, "verify") else None


def available() -> bool:
    return _health() is not None


def state_reads(m):
    """MS 상태 관리자 → state-export `read` 꼴 `(실체, 상태) -> {value, status, freshness, observed_at, time_base} | None`."""
    def read(entity, state):
        node = m.graph.nodes.get(entity)
        v = node.props.get(state) if node is not None else None
        if v is None:
            return None
        return {"entity": entity, "name": state, "value": v.value, "status": "DERIVED" if v.derived else "OBSERVED",
                "freshness": "STALE" if m.is_stale(entity, state) else "FRESH", "observed_at": v.ts * 1000,
                "time_base": TIME_BASE}
    return read


class Verifier:
    def __init__(self, registry, run_state=None):
        self.h = _health()
        self.reg, self.run_state = registry, run_state
        self.pending: list = []                       # [(decision_ref, command, run, outcome)] -- PENDING 이었던 것

    def _args(self, command) -> "dict | None":
        """verify 의 spec · postcondition · window_ms. 사후조건이 없으면 None -- 부르지 않는다."""
        from action.spec import verify_args
        spec = self.reg.model.get(command.action)
        if spec is None or not spec.postcondition:
            return None
        return verify_args(spec)

    def _reads(self, m):
        mine = state_reads(m)
        other = getattr(self.run_state, "read", None)

        def read(entity, state):
            if entity in m.graph.nodes:
                return mine(entity, state)
            return other(entity, state) if other is not None else None
        return read

    def _subjects(self, run) -> dict:
        return dict(self.run_state.subjects(run)) if self.run_state is not None else {}

    def _verify(self, command, run, outcome, m, now_ms, args):
        return self.h.verify(command, run=run, subjects=self._subjects(run), reads=self._reads(m), evaluated_at=now_ms,
                             outcome=outcome, outcome_ref=None, **args)

    def after_execute(self, command, run, decision_ref, outcome, m, now_ms) -> "dict | None":
        args = self._args(command)
        if args is None:                              # 사후조건 없음 -- 부르지 않는다
            return None
        rec = self._verify(command, run, outcome, m, now_ms, args)
        if not rec.final:
            self.pending.append((decision_ref, command, run, outcome))
        return rec.to_dict()

    def close_windows(self, m, now_ms) -> list:
        """창이 닫힌(now ≥ end) PENDING 을 다시 판정한다. 열려 있는 것은 그대로 기다린다."""
        out, keep = [], []
        for item in self.pending:
            decision_ref, command, run, outcome = item
            args = self._args(command)               # 기다리는 것은 사후조건이 있던 것뿐이다
            if now_ms < command.issued_at + args["window_ms"]:
                keep.append(item)
                continue
            out.append((decision_ref, self._verify(command, run, outcome, m, now_ms, args).to_dict()))
        self.pending = keep
        return out
