"""MS API · Request Manager -- 요청 하나를 받아 정책 루프를 한 번 돈다.

    USER -> handle(request)
      1. 세션의 **상태**를 읽는다(기본 usage_model.snapshot -- 파생 상태만. `state_reader` 로 바꿔 꽂을 수 있다)
      2. Context Policy · Prompt Policy · Provider Policy 가 상태에서 계획을 고른다(판본 붙음)
      3. Pipeline: State Query -> Context Policy -> Prompt Policy -> Provider Adapter -> Proposal -> Arbiter -> Tool
      4. 실행을 **정규 텔레메트리**(RunRecord)로 적는다
      5. 그 텔레메트리를 State Manager 에 넣는다 -> 모형이 다음 상태를 정한다   (적응 루프가 닫힌다)

MS 는 provider 가 아니다. 추론은 provider 가 하고, MS 는 그 위에서 무엇을 보일지 · 어떻게 말할지 · 누구에게 물을지 · 제안을 받을지를
정한다. 연구 대상은 이 고리다: Telemetry -> State -> Context/Prompt Policy -> Provider -> LLM -> Arbiter.

세계의 상태(서버 · 랙)와 사용의 상태(세션)는 같은 State Manager 에 둘 수도, 따로 둘 수도 있다(`usage_manager`). 평가는 과업마다
세계를 새로 짓고 세션 상태는 이어 가야 해서 따로 둔다. 어느 쪽이든 같은 Model · Relationship 구조다.

**상태 읽기의 자리(`state_reader`).** 기본은 `usage_model.snapshot` 이다. Decision Context 층(cogito5170/DC)처럼 신선도 · 근거를
검사하는 쪽을 꽂으려면 `state_reader(usage_manager, sid[, request]) -> {"state": {상태: 값 | None}, "record": {...} | None
[, "queries": {질의 이름: core 질의}]}` 을 준다. 인자를 셋 받는 리더에는 요청을 넘긴다(요청의 `queries` 를 DC 가 돌리게, PC-23).
`queries` 를 돌려주면 CR 은 그래프에 직접 묻지 않고 그 결과(값 · 유효성은 DC 가 정한 그대로)로 맥락을 짓는다.
MS 는 그 패키지를 import 하지 않는다. 받은 `state` 는 사용 상태 이름(STATES)과 스칼라 값만 허락한다 -- 원 측정 · 객체를 정책 쪽으로
몰래 넣지 못한다. `record`(예: 결정 문맥의 id · digest)는 **결정 기록**(`DecisionRecord.state_source`)에 남는다 -- 실행 기록(텔레메트리)이 아니다.

**시계는 하나다**(PC-12): 세계 State Manager 의 시계. 사용 상태를 따로 두면(`usage_manager`) 같은 시계여야 한다 -- 아니면 거절한다.
실행 시각 · 텔레메트리 시각 · 실행 id 가 모두 그 시계에서 나온다. 걸린 시간(`wall`, provider 의 지연)은 단조 시계로 재는 **길이**다.

**과업 성공은 Runtime 이 판정하지 않는다**(baseline PC-13 · BV-10). 판정은 성공 기준을 가진 쪽(평가 하니스 `ms.eval.judge`)의 일이다.
Runtime 은 요청의 `success` · `forbidden` · `expect_noop` 을 읽지 않고, 실행 기록의 `task_success` 는 None 으로 둔다. 판정한 쪽이
`evaluation(run_id, task_success)` 로 돌려주면 그것을 사람의 고침(`feedback`)처럼 **바깥에서 온 결과 관측**으로 넣는다.
"""
from __future__ import annotations

import inspect
import itertools
import json
import time

from . import usage_model as U
from .arbiter import DENY, Arbiter
from .cr import VERSION as CR_VERSION, ContextRuntime
from .pipeline import Pipeline
from . import dispatch as D, guard_shadow, intent, l0, verify as V
from .decision_record import DecisionRecord
from .policy import BASE_CONTEXT, ExplicitProvider, FixedContext, FixedPrompt, default_context_plan, undecided
from .run_telemetry import RunRecord, cost_of
from .tools import RETRIEVE



_SCALAR = (str, int, float, bool, type(None))


def _check_supplied(supplied: dict, names: list):
    """결정 문맥이 준 질의 결과의 꼴 -- 요청한 질의 전부 · 행마다 id · model · props {속성: [스칼라, 유효성 글자]}. 객체를 몰래 못 넣는다."""
    if not isinstance(supplied, dict) or set(supplied) != set(names):
        raise ValueError(f"state_reader 의 질의 결과가 요청 질의와 다르다: {sorted(supplied) if isinstance(supplied, dict) else supplied} != {sorted(names)}")
    for name, q in supplied.items():
        if not isinstance(q.get("rows"), list) or not isinstance(q.get("matched"), int):
            raise ValueError(f"질의 {name}: rows · matched 가 없다")
        for r in q["rows"]:
            if not isinstance(r.get("id"), str) or not isinstance(r.get("props"), dict):
                raise ValueError(f"질의 {name}: 행의 꼴이 아니다")
            for p, pv in r["props"].items():
                if not (isinstance(pv, (list, tuple)) and len(pv) == 2 and isinstance(pv[0], _SCALAR) and isinstance(pv[1], str)):
                    raise ValueError(f"질의 {name}: {r['id']}.{p} 는 [스칼라 값, 유효성] 이어야 한다")
    json.dumps(supplied)


class Runtime:
    def __init__(self, manager, registry, providers: dict, *, grants=(), context_selector=None, prompt_selector=None,
                 provider_policy=None, base_context: "dict | None" = None, prices: "dict | None" = None,
                 ledger_path: "str | None" = None, max_rounds: int = 4, wall=time.perf_counter,
                 usage_manager=None, prompt_layout: "str | None" = None, l0_ledger: "str | None" = None,
                 l0_sink=None, state_reader=None, run_state=None, guard_mode: str = "shadow", risky=None):
        self.m, self.reg, self.providers = manager, registry, dict(providers)
        self.um = usage_manager or manager
        if self.um.clock is not manager.clock:
            raise ValueError("시계가 둘이다: usage_manager 와 manager 는 같은 시계를 써야 한다(PC-12)")
        self.clock = manager.clock
        from .prompt import DEFAULT_LAYOUT, TEMPLATE_VERSIONS
        self.prompt_layout = prompt_layout or DEFAULT_LAYOUT
        self.template_version = TEMPLATE_VERSIONS[self.prompt_layout]
        U.install(self.um)
        self.arbiter = Arbiter(registry, grants, clock=self.clock)
        # Guard(CMD-M17 shadow · CMD-M24 enforce). shadow: 기록만(기본). enforce: 실행 = Arbiter ALLOW ∧ Guard ALLOW,
        # snapshot 길은 실행하지 않는다(BD-114). enforce 인데 guard 가 없으면 런타임이 서지 않는다(닫는 쪽)
        if guard_mode not in ("shadow", "enforce"):
            raise ValueError(f"guard_mode={guard_mode!r}: shadow · enforce 가운데 하나")
        self.guard_mode = guard_mode
        # risky: Guard D 가 막는 위험 등급(SDK 가 꽂는 자리, CMD-M26). None 이면 Guard 의 기본(external · irreversible)
        self.guard = guard_shadow.Shadow(registry, grants, guard_mode, risky) if guard_shadow.available() else None
        if guard_mode == "enforce" and (self.guard is None or not D.available()):
            raise ImportError("guard_mode=enforce 인데 guard(또는 action 실행기)를 불러올 수 없다 -- 런타임을 세우지 않는다")
        self.dispatch = D.Dispatch(registry) if D.available() else None   # DC 길 실행기(CMD-M20 shadow → M22 execute)
        # Health VERIFY(CMD-M23, 선택 의존). run_state: $run.* 읽기 · subjects 를 줄 Sensor state-export 이음매(없으면 None)
        self.verifier = V.Verifier(registry, run_state) if V.available() else None
        self.ctx_sel = context_selector or FixedContext()
        self.prompt_sel = prompt_selector or FixedPrompt()
        default = next(iter(self.providers)) if self.providers else None
        self.provider_policy = provider_policy or ExplicitProvider(default)
        self.base_context = dict(BASE_CONTEXT, **(base_context or {}))
        self.prices, self.ledger_path, self.max_rounds, self.wall = prices, ledger_path, max_rounds, wall
        self.records: dict = {}
        self._run_ids = itertools.count(1)   # 실행 id 셈 -- 이 Runtime 의 것(CMD-M26). 모듈 전역이면 다른 Runtime 이 바꾼다
        self.decisions: dict = {}
        self.intents: dict = {}         # 결정 id -> ActionIntent 기록(shadow, CMD-M15). 결정 기록 밖에 둔다
        self.guards: dict = {}          # 결정 id -> GuardResult 기록(shadow, CMD-M17). 결정 기록 밖에 둔다
        self.executions: dict = {}      # 결정 id -> 실행기 기록(CMD-M20 · M22). 결정 기록 밖에 둔다
        self.verifications: dict = {}   # 결정 id -> VerificationRecord 들(CMD-M23). 결정 기록 밖에 둔다
        self.state_reader = state_reader
        self.l0_ledger, self.l0_sink = l0_ledger, l0_sink      # L0 Telemetry(선택 의존). 둘 다 없으면 안 낸다
        if (l0_ledger or l0_sink) and not l0.available():
            raise ImportError("l0_ledger · l0_sink 를 주었는데 L0 Telemetry 가 없다 -- "
                              "pip install git+https://github.com/cogito5170/Telemetry")

    def open_session(self, name: str, budgets: dict) -> str:
        return U.open_session(self.um, name, budgets)

    def close_windows(self) -> list:
        """창이 닫힌 PENDING 검증을 다시 판정해 원장에 남긴다(CMD-M23). 요청마다 먼저 불리고, 바깥에서도 부를 수 있다."""
        if self.verifier is None:
            return []
        out = []
        for decision_ref, rec in self.verifier.close_windows(self.m, self.clock() * 1000):
            self.verifications.setdefault(decision_ref, []).append(rec)
            self._ledger({"kind": "verification", "decision_ref": decision_ref, "when": "window_close", "record": rec})
            out.append({"decision_ref": decision_ref, "when": "window_close", "record": rec})
        return out

    def _verify_after(self, executions, run_id, decision_ref) -> list:
        """실행기가 실행한 명령마다, 관측을 넣은 직후 VERIFY. 판정은 기록만 한다."""
        if self.verifier is None:
            return []
        from action.forms import ActionCommand, ActionOutcome
        out = []
        for x in executions:
            ex = x.get("execution") or {}
            if not ex.get("executed") or x.get("command") is None:
                continue
            outcome = ActionOutcome.from_dict(ex["outcome"]) if ex.get("outcome") else None
            rec = self.verifier.after_execute(ActionCommand.from_dict(x["command"]), run_id, decision_ref, outcome,
                                              self.m, self.clock() * 1000)
            if rec is not None:                       # 사후조건 없는 행동은 검증하지 않는다(기록도 없다)
                out.append({"round": x["round"], "when": "after_execute", "record": rec})
        return out

    def handle(self, request: dict) -> dict:
        t0 = self.wall()
        self.close_windows()
        sid = U.session_id(request["session"])
        if sid not in self.um.graph.nodes:
            raise KeyError(f"세션 {request['session']} 이 열리지 않았다(open_session)")
        state, source, supplied = self._read_state(sid, request)
        mat = guard_shadow.material(self.state_reader) if self.guard and intent.dc_id(source) else None   # 읽은 직후
        plan = ContextRuntime.plan(state, self.ctx_sel, self.prompt_sel, self.base_context, self.prompt_layout)
        action = source.get("default_action")
        if action is not None and undecided(self.ctx_sel, state):
            # BD-76: 규칙이 정해지지 않으면 결정 문맥이 준 기본 결정. 지금 선택기는 그때 이미 고정과 같은 계획을 내므로
            # 보이는 맥락은 그대로다(시험) -- 계획 이유와 판본만 기본 결정으로 남는다
            plan["context_policy"] = default_context_plan(action, self.base_context)
        cplan, pplan = plan["context_policy"], plan["prompt_policy"]
        choice = self.provider_policy.select(state, request)
        provider = self.providers[choice["provider"]]
        run_id = request.get("run_id") or f"run-{next(self._run_ids)}-{int(self.clock() * 1000) % 10**8}"
        l0rec = l0.recorder(run_id, self.l0_ledger, self.l0_sink, wall=lambda: self.clock() * 1000)   # L0 도 Runtime 시계(ms)
        l0rec.run_start(model=choice.get("model"), provider=choice["provider"])
        pre = []                  # 도구를 실행하면 결정 기록은 그 직전에 지어진다(PC-19 G1 -- ActionCommand.decision_ref 의 자리)
        guards, executions, by_round = [], [], {}

        def decided(rnd, p, ctx, d):
            """Arbiter 판정 직후: 이 판의 의도 · Arbiter 판정을 붙잡고, Guard 가 있으면 같은 지금 상태로 부른다(기록만)."""
            it = intent.intent_of(source, p.to_dict())
            if it is None or isinstance(it, list):
                return
            g = None
            if self.guard:
                guards.append({"round": rnd, "intent_id": it.id, "arbiter": [d.verdict, d.rule],
                               "guard": self.guard.check(it, mat, ctx, self.m)})
                g = guards[-1]["guard"]
            by_round[rnd] = (it, d.verdict, g)

        def before_execute(r):
            pre.append(self._decision(state, cplan, pplan, choice, r, source))
            return pre[-1].id

        enforce = self.guard_mode == "enforce"

        def dispatch(rnd, p, decision_ref):
            """도구 호출 자리(DC 길): 실행기로 실행하고 관측을 돌려준다. L0 에는 action.* 한 쌍만(tool.* 없음).
            재료: shadow 는 Arbiter ALLOW 의도(BD-111), enforce 는 guard command_material(E3)."""
            it, verdict, g = by_round.get(rnd, (None, None, None))
            entry, obs = self.dispatch.run(it, verdict, g, decision_ref, self.clock() * 1000, l0rec,
                                           source="guard" if enforce else "arbiter")
            executions.append({"round": rnd, **entry})
            return obs

        def gate(rnd):
            """enforce: 실행해도 되나. 막는 까닭(문자열) 또는 None. DC 길은 Guard ALLOW · SAFE_ACTION 이고 실행기가 그 명령을
            받을 때만, snapshot 길은 언제나 막는다(BD-114 의 2). 막으면 실행기 · L0 action.* · VERIFY 가 없다."""
            if not intent.dc_id(source):
                return "enforce: snapshot 길은 실행하지 않는다(BD-114)"
            it, verdict, g = by_round.get(rnd, (None, None, None))
            if g is None:
                return "enforce: Guard 결과가 없다(의도 없음)"
            if g["verdict"] not in ("ALLOW", "SAFE_ACTION"):
                return f"enforce: Guard {g['verdict']}({g['rule']})"
            cmd, why = self.dispatch.command(it, verdict, "dec-gate", self.clock() * 1000, g, source="guard")
            if cmd is None:
                return f"enforce: 명령을 지을 수 없다 -- {why}"
            if self.dispatch.model.get(cmd.action) is None or cmd.action not in self.dispatch.handlers:
                return f"enforce: 실행기가 {cmd.action} 를 받지 않는다"      # 지금 길로 돌아가지 않게 미리 막는다
            return None
        pipe = Pipeline(self.m, self.reg, provider, None, self.arbiter,
                        model=choice.get("model"), stream=bool(request.get("stream")),
                        tool_mode=request.get("tool_mode", "text"), preamble=request.get("preamble", ""),
                        cr=ContextRuntime.from_plan(self.reg, plan), recorder=l0rec,
                        provider_label=choice["provider"],
                        before_execute=before_execute,
                        after_decide=decided if intent.dc_id(source) and (self.guard or self.dispatch) else None,
                        dispatch=dispatch if self.dispatch and intent.dc_id(source) else None,
                        gate=gate if enforce else None)
        res = pipe.run(request["task"], request.get("queries", ()), max_rounds=request.get("max_rounds", self.max_rounds),
                       supplied=supplied)
        total_ms = (self.wall() - t0) * 1000
        dec = pre[0] if pre else self._decision(state, cplan, pplan, choice, res, source)
        rec = self._record(request, choice, provider, res, total_ms, run_id, dec)
        intents = intent.intents(source, res.rounds)
        verifications = self._verify_after(executions, run_id, dec.id)
        # 끝 요약: 런타임(MS)이 아는 사실만. 비용은 provider 가 보고했을 때만(가격표 계산은 L0 가 아니다)
        l0rec.run_end(decision_ref=dec.id, terminal_reason=res.outcome, num_turns=len(res.rounds),
                      run_duration_ms=round(total_ms, 3), model=rec.run["model"],
                      cost_usd=rec.cost["usd"] if rec.cost["source"] == "provider" else None)
        U.link_provider(self.um, sid, choice["provider"])
        matched = res.rounds[0]["context"]["matched"] if res.rounds and "context" in res.rounds[0] else None
        for sig in rec.to_signals(sid, self.clock(), matched_rows=matched):
            self.um.ingest(sig)
        self.records[rec.run["run_id"]] = rec
        self.decisions[dec.id] = dec
        self._ledger({"kind": "decision", "decision": dec.to_dict()})     # 결정 먼저, 그 결정이 낳은 실행은 id 로 잇는다
        if intents:
            self.intents[dec.id] = intents
        for it in intents:
            self._ledger({"kind": "intent", "decision_ref": dec.id, **it})
        if guards:
            self.guards[dec.id] = guards
        for g in guards:
            self._ledger({"kind": "guard", "decision_ref": dec.id, **g})
        if executions:
            self.executions[dec.id] = executions
        for x in executions:
            self._ledger({"kind": "execution", "decision_ref": dec.id, **x})
        if verifications:
            self.verifications.setdefault(dec.id, []).extend(v["record"] for v in verifications)
        for v in verifications:
            self._ledger({"kind": "verification", "decision_ref": dec.id, **v})
        self._ledger({"kind": "run", "record": rec.to_dict()})
        return {"run_id": rec.run["run_id"], "record": rec.to_dict(), "decision": dec.to_dict(), "result": res.to_dict(),
                "intents": intents, "guards": guards, "executions": executions, "verifications": verifications}

    def _read_state(self, sid: str, request: "dict | None" = None):
        """정책 · CR 이 볼 상태와 그 출처(, 결정 문맥이 돌린 질의 결과). 꽂은 읽기가 무엇을 주든 사용 상태 이름과 스칼라 값만,
        질의 결과는 정해진 꼴(행 · 스칼라 값 · 유효성 글자)만 통과시킨다."""
        if self.state_reader is None:
            return U.snapshot(self.um, sid), {"kind": "usage_model.snapshot", "model_version": U.MODEL_VERSION}, None
        try:
            takes_request = len(inspect.signature(self.state_reader).parameters) >= 3
        except (TypeError, ValueError):
            takes_request = False
        out = self.state_reader(self.um, sid, request) if takes_request else self.state_reader(self.um, sid)
        state, record = dict(out["state"]), out.get("record")
        allowed = set(U.STATES) | {"model_version", "decision_context"}
        bad = [k for k, v in state.items() if k not in allowed or not isinstance(v, (str, int, float, bool, type(None)))]
        if bad:
            raise ValueError(f"state_reader 가 사용 상태가 아닌 것을 줬다: {sorted(bad)}")
        state = {"model_version": state.get("model_version", U.MODEL_VERSION), **{s: state.get(s) for s in U.STATES},
                 **({"decision_context": state["decision_context"]} if "decision_context" in state else {})}
        json.dumps(record)        # 기록에 남길 수 있어야 한다
        supplied = out.get("queries")
        if supplied is not None:
            _check_supplied(supplied, [q["name"] if isinstance(q, dict) else q.name for q in (request or {}).get("queries", ())])
        src = {"kind": "state_reader", **(record or {})}
        if supplied is not None:
            src["queries"] = sorted(supplied)
        return state, src, supplied

    def feedback(self, run_id: str, user_correction: bool):
        """사람이 그 실행을 고쳤나. 그것도 텔레메트리다 -- correction_rate 상태가 여기서 나온다."""
        rec = self.records[run_id]
        rec.outcome["user_correction"] = bool(user_correction)
        sid = U.session_id(rec.run["session_id"])
        r = self.um.ingest({"source": "user", "entity": sid, "signal": "outcome.user_correction",
                            "value": bool(user_correction), "ts": self.clock(), "meta": {"run_id": run_id}})
        self._ledger({"kind": "feedback", "run_id": run_id, "user_correction": bool(user_correction)})
        return r

    def evaluation(self, run_id: str, task_success: bool):
        """성공 기준을 가진 쪽(평가 하니스)이 그 실행을 판정한 결과. Runtime 은 판정하지 않고 받아 넣기만 한다 --
        answer_reliability 상태가 여기서 나온다. 한 실행에 한 번만."""
        rec = self.records[run_id]
        if rec.outcome.get("task_success") is not None:
            raise ValueError(f"{run_id} 는 이미 판정을 받았다")
        rec.outcome["task_success"] = bool(task_success)
        sid = U.session_id(rec.run["session_id"])
        r = self.um.ingest({"source": "evaluation", "entity": sid, "signal": "outcome.task_success",
                            "value": bool(task_success), "ts": self.clock(), "meta": {"run_id": run_id}})
        self._ledger({"kind": "evaluation", "run_id": run_id, "task_success": bool(task_success)})
        return r

    # -- 기록 ---------------------------------------------------------------------------------------------------
    def _decision(self, state, cplan, pplan, choice, res, state_source=None) -> DecisionRecord:
        """결정 기록. 입력(상태 · 계획 · 중재 결정)은 도구 실행 전에 다 정해진다 -- 그래서 실행 직전에 지어도 id 가 같다."""
        decisions = [r["decision"] for r in res.rounds if "decision" in r]
        return DecisionRecord(
            cr=CR_VERSION, state=state, context_policy=cplan, prompt_policy=pplan, provider_policy=choice,
            arbiter_decision={"final": decisions[-1] if decisions else None,
                              "all": [[d["verdict"], d["rule"]] for d in decisions]},
            inputs={"base_context": self.base_context, "default_provider": self.provider_policy.default
                    if isinstance(self.provider_policy, ExplicitProvider) else None},
            state_source=state_source or {})

    def _record(self, request, choice, provider, res, total_ms, run_id, dec: DecisionRecord) -> RunRecord:
        calls = res.calls
        decisions = [r["decision"] for r in res.rounds if "decision" in r]
        proposals = [r["proposal"] for r in res.rounds if "proposal" in r]

        def tsum(key):
            vals = [c["usage"][key] for c in calls]
            return None if not vals or any(v is None for v in vals) else sum(vals)

        def share(chars_key):
            vals = []
            for c in calls:
                i = c["usage"]["input_tokens"]
                if i is None or not c["prompt_chars"]:
                    return None
                vals.append(i * c[chars_key] / c["prompt_chars"])
            return round(sum(vals)) if vals else None

        denies = sum(1 for d in decisions if d["verdict"] == DENY)
        invalid = sum(1 for d in decisions if d["rule"] == "A0")
        retrievals = sum(1 for d, p in zip(decisions, proposals) if d["verdict"] == "ALLOW" and p["tool"] == RETRIEVE)
        retries = sum(1 for d in decisions[:-1] if d["verdict"] == DENY) if len(decisions) > 1 else 0
        tool_ok = None
        if res.executed:
            tool_ok = not any(i["signal"] == "tool_error" for i in res.ingested)
        tokens = {"input_tokens": tsum("input_tokens"), "output_tokens": tsum("output_tokens"),
                  "cached_input_tokens": tsum("cached_input_tokens"), "context_tokens": share("context_chars"),
                  "retrieved_tokens": share("retrieved_chars"), "total_tokens": tsum("total_tokens")}
        if tokens["total_tokens"] is None and tokens["input_tokens"] is not None and tokens["output_tokens"] is not None:
            tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
        inf = [c["inference_ms"] for c in calls]
        latency = {"ttft_ms": calls[0]["ttft_ms"] if calls else None,
                   "inference_ms": None if not inf or any(v is None for v in inf) else round(sum(inf), 3),
                   "total_ms": round(total_ms, 3)}
        reported = [c["cost_usd"] for c in calls]
        if calls and all(v is not None for v in reported):
            cost = {"usd": sum(reported), "source": "provider"}
        else:
            c = cost_of(calls[0]["model"] if calls else "", tokens, self.prices)
            cost = {"usd": c, "source": "price_table" if c is not None else None}
        exts = {}
        for c in calls:
            for k, v in (c["extensions"] or {}).items():
                exts.setdefault(k, []).append(v)
        unsupported = sorted({u for c in calls for u in c["unsupported"]})
        return RunRecord(
            run={"run_id": run_id, "session_id": request["session"], "provider": choice["provider"],
                 "model": calls[0]["model"] if calls else choice.get("model"), "timestamp": self.clock(),
                 "simulated": bool(getattr(provider, "simulated", False))},
            tokens=tokens, latency=latency,
            interaction={"llm_calls": len(calls), "tool_calls": len(res.executed), "retries": retries,
                         "context_retrievals": retrievals, "arbiter_denies": denies, "proposal_invalid": invalid,
                         "non_progress_rounds": denies + retrievals},
            outcome={"task_success": None, "user_correction": None,          # 판정은 evaluation() 으로 바깥에서
                     "tool_success": tool_ok},
            decision_ref=dec.id,
            cost=cost,
            estimated={"context_tokens": "input_tokens × context_chars/prompt_chars",
                       "retrieved_tokens": "input_tokens × retrieved_chars/prompt_chars"},
            unsupported=unsupported, extensions=exts)

    def _ledger(self, rec: dict):
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
