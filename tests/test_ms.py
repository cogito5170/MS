import json
import os
import subprocess
import sys
import tempfile
import unittest

from ms import (ALLOW, APPLIED, DENY, KEEP, NOOP, REJECTED, RETRIEVE, STALE, SUMMARIZE, UNBOUND, ContextPolicy,
                Model, ModelError, Pipeline, ScriptedLLM, StateManager, StateQuery, Telemetry, ToolRegistry,
                Arbiter, build_prompt, parse_proposal, run_query, tool_query)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "ms", "examples", "datacenter.json")
TELE = os.path.join(ROOT, "ms", "examples", "datacenter_telemetry.jsonl")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def world(budget=1500, clock=None, grants=()):
    with open(SPEC, encoding="utf-8") as f:
        spec = json.load(f)
    clock = clock or Clock(spec["now"])
    m = StateManager.from_spec(spec, clock=clock)
    with open(TELE, encoding="utf-8") as f:
        for line in f:
            m.ingest(json.loads(line))
    reg = ToolRegistry(spec["tools"])
    pol = ContextPolicy(budget_chars=budget, summarize_min=3)
    arb = Arbiter(reg, grants, clock=clock)
    return spec, m, reg, pol, arb, clock


def ctx_of(spec, m, reg, pol, queries=None, task="t", retrieved=()):
    p = Pipeline(m, reg, ScriptedLLM([]), pol)
    return p.context(task, queries if queries is not None else spec["queries"], retrieved)


def P(**kw):
    return parse_proposal(json.dumps(kw))


# -- 원칙 1: Telemetry 는 State 가 아니다 ---------------------------------------
class TelemetryIsNotState(unittest.TestCase):
    def test_unknown_signal_never_enters_graph(self):
        _, m, *_ = world()
        before = m.graph.dump()
        r = m.ingest(Telemetry("env", "srv02", "humidity", 41, ts=999))
        self.assertEqual(r.status, UNBOUND)
        self.assertEqual(m.graph.dump(), before)
        self.assertNotIn("humidity", m.graph.nodes["srv02"].props)
        self.assertEqual(m.quarantine[-1]["signal"], "humidity")

    def test_unknown_entity_is_not_created(self):
        _, m, *_ = world()
        r = m.ingest(Telemetry("bmc", "srv99", "cpu_temp", 70, ts=999))
        self.assertEqual(r.status, UNBOUND)
        self.assertNotIn("srv99", m.graph.nodes)

    def test_out_of_range_keeps_old_state(self):
        _, m, *_ = world()
        old = m.graph.nodes["srv03"].props["temp_c"].value
        r = m.ingest(Telemetry("bmc", "srv03", "cpu_temp", 999, ts=999))
        self.assertEqual(r.status, REJECTED)
        self.assertEqual(m.graph.nodes["srv03"].props["temp_c"].value, old)

    def test_wrong_type_rejected(self):
        _, m, *_ = world()
        self.assertEqual(m.ingest(Telemetry("bmc", "srv03", "cpu_temp", "hot", ts=999)).status, REJECTED)
        self.assertEqual(m.ingest(Telemetry("bmc", "srv03", "fan_state", "melted", ts=999)).status, REJECTED)

    def test_late_arrival_does_not_overwrite(self):
        _, m, *_ = world()
        # 예시 텔레메트리의 마지막 줄: srv01 95°C 인데 ts=950 < 기존 990
        self.assertEqual(m.graph.nodes["srv01"].props["temp_c"].value, 61.0)
        self.assertEqual(m.counts[STALE], 1)

    def test_provenance_points_to_telemetry(self):
        _, m, *_ = world()
        t = Telemetry("bmc", "srv02", "cpu_temp", 70, ts=999)
        m.ingest(t)
        self.assertEqual(m.graph.nodes["srv02"].props["temp_c"].src, t.id)
        self.assertTrue(m.graph.nodes["srv02"].props["status"].src.startswith("derived:"))

    def test_tool_result_is_observation_not_state(self):
        # reboot 이 "reboot_ack" 를 돌려줘도 모형이 모르는 신호라 상태가 아니다
        spec, m, reg, pol, _, clock = world(grants=("reboot",))
        llm = ScriptedLLM([{"tool": "reboot", "target": "srv07"}])
        res = Pipeline(m, reg, llm, pol, Arbiter(reg, {"reboot"}, clock=clock)).run("t", spec["queries"])
        self.assertEqual(res.outcome, "executed")
        self.assertEqual(res.ingested[0]["status"], UNBOUND)
        self.assertNotIn("reboot_ack", m.graph.nodes["srv07"].props)

    def test_tool_result_enters_through_model(self):
        spec, m, reg, pol, arb, _ = world()
        llm = ScriptedLLM([{"tool": "throttle", "target": "srv07", "args": {"level": 2}}])
        res = Pipeline(m, reg, llm, pol, arb).run("t", spec["queries"])
        self.assertEqual(res.ingested[0]["status"], APPLIED)
        self.assertIs(m.graph.nodes["srv07"].props["throttled"].value, True)
        self.assertTrue(m.graph.nodes["srv07"].props["throttled"].src.startswith("t"))

    def test_tool_crash_becomes_telemetry(self):
        spec, m, reg, pol, arb, _ = world()
        reg.bind("throttle", lambda target, args: 1 / 0)
        llm = ScriptedLLM([{"tool": "throttle", "target": "srv07", "args": {"level": 1}}])
        res = Pipeline(m, reg, llm, pol, arb).run("t", spec["queries"])
        self.assertEqual(res.ingested[0]["signal"], "tool_error")
        self.assertEqual(res.ingested[0]["status"], UNBOUND)


# -- 원칙 2: State 의 뜻은 Model 이 정한다 ----------------------------------------
class ModelDefinesMeaning(unittest.TestCase):
    def _m(self, transform):
        return Model.from_dict({"name": "S", "properties": {"temp_c": {"type": "number", "min": -40, "max": 150}},
                                "bindings": [{"signal": "t", "property": "temp_c", "transform": transform}],
                                "derived": {"status": {"cases": [{"when": [["temp_c", ">=", 90]], "value": "critical"}],
                                                       "default": "normal"}}})

    def test_same_telemetry_two_models_two_states(self):
        out = {}
        for name, tf in (("celsius", []), ("fahrenheit", [["offset", -32], ["scale", 5 / 9], ["round", 1]])):
            m = StateManager([self._m(tf)], clock=Clock())
            m.declare("x", "S")
            self.assertEqual(m.ingest(Telemetry("s", "x", "t", 100, ts=999)).status, APPLIED)
            out[name] = m.graph.nodes["x"].values()
        self.assertEqual(out["celsius"], {"temp_c": 100.0, "status": "critical"})
        self.assertEqual(out["fahrenheit"], {"temp_c": 37.8, "status": "normal"})

    def test_derived_unknown_when_input_missing(self):
        _, m, *_ = world()
        # srv11 의 온도는 범위 밖이라 거절됐다 -- 상태는 'normal' 이 아니라 모름
        self.assertNotIn("temp_c", m.graph.nodes["srv11"].props)
        self.assertNotIn("status", m.graph.nodes["srv11"].props)

    def test_bad_models_refused(self):
        with self.assertRaises(ModelError):
            Model.from_dict({"name": "S", "properties": {}, "bindings": [{"signal": "a", "property": "nope"}]})
        with self.assertRaises(ModelError):
            Model.from_dict({"name": "S", "properties": {"a": {"type": "number"}},
                             "derived": {"d": {"cases": [{"when": [["a", "~=", 1]], "value": 1}]}}})
        with self.assertRaises(ModelError):
            Model.from_dict({"name": "S", "properties": {"a": {"type": "enum"}}})

    def test_relationship_cardinality(self):
        _, m, *_ = world()
        with self.assertRaises(ModelError):            # 1:N -- srv01 은 이미 rack1 에 있다
            m.relate("contains", "rack2", "srv01")
        with self.assertRaises(ModelError):            # 모형이 거꾸로
            m.relate("contains", "srv01", "rack1")

    def test_staleness_from_model_ttl(self):
        _, m, *_ = world()
        self.assertTrue(m.is_stale("srv04", "temp_c"))      # ts 900, ttl 60, now 1000
        self.assertTrue(m.is_stale("srv04", "status"))      # 파생은 입력의 ttl 을 물려받는다
        self.assertFalse(m.is_stale("srv03", "temp_c"))


# -- 원칙 3: LLM 에는 Query 결과만 ----------------------------------------------
class OnlyQueryResultsReachLLM(unittest.TestCase):
    Q = [{"name": "q1", "model": "Server", "related": {"rel": "contains", "dir": "out", "of": "rack1"},
          "select": ["temp_c", "status"]}]

    def test_prompt_has_no_entity_outside_results(self):
        spec, m, reg, pol, arb, _ = world(budget=100000)
        llm = ScriptedLLM([{"tool": "none"}])
        Pipeline(m, reg, llm, pol, arb).run("t", self.Q)
        prompt = llm.prompts[0]
        inside = {f"srv0{i}" for i in range(1, 7)}
        for nid in m.graph.nodes:
            if nid in inside:
                self.assertIn(f'"{nid}"', prompt)         # 양성 대조 -- 검사가 헛돌지 않는다
            else:
                self.assertNotIn(f'"{nid}"', prompt, nid)
        self.assertNotIn("rack1", prompt)                 # 질의의 기준점도 결과가 아니면 안 실린다

    def test_unselected_props_not_in_prompt(self):
        spec, m, reg, pol, arb, _ = world(budget=100000)
        llm = ScriptedLLM([{"tool": "none"}])
        Pipeline(m, reg, llm, pol, arb).run("t", self.Q)
        self.assertNotIn('"fan"', llm.prompts[0])
        self.assertIn('"temp_c"', llm.prompts[0])

    def test_context_never_dumps_graph(self):
        spec, m, reg, pol, arb, _ = world()

        def boom():
            raise AssertionError("맥락 쪽이 그래프 전체를 꺼냈다")
        m.graph.dump = boom
        llm = ScriptedLLM([{"tool": "retrieve", "target": "h1"}, {"tool": "none"}])
        Pipeline(m, reg, llm, pol, arb).run("t", spec["queries"])
        self.assertEqual(len(llm.prompts), 2)

    def test_llm_input_is_exactly_the_prompt(self):
        spec, m, reg, pol, arb, _ = world()
        ctx = ctx_of(spec, m, reg, pol)
        llm = ScriptedLLM([{"tool": "none"}])
        Pipeline(m, reg, llm, pol, arb).run("t", spec["queries"], max_rounds=1)
        self.assertEqual(llm.prompts[0], build_prompt(ctx))

    def test_edges_only_inside_results(self):
        spec, m, *_ = world()
        r = run_query(StateQuery("q", ids=["rack1", "srv01", "srv07"]), m)
        edges = [e for row in r.rows for e in row.edges]
        self.assertEqual(edges, [["contains", "rack1", "srv01"]])   # srv07 은 rack2 -- 관계가 결과 밖이다


# -- 맥락 정책 ---------------------------------------------------------------------
class Policy(unittest.TestCase):
    def test_budget_is_measured_on_render(self):
        for budget in (800, 1200, 1500, 3000):
            spec, m, reg, pol, *_ = world(budget=budget)
            ctx = ctx_of(spec, m, reg, pol)
            if not ctx.over_budget:
                self.assertLessEqual(len(ctx.render()), budget)
            self.assertEqual(ctx.stats()["chars"], len(ctx.render()))

    def test_every_row_accounted_for(self):
        for budget in (200, 900, 1500, 100000):
            spec, m, reg, pol, *_ = world(budget=budget)
            ctx = ctx_of(spec, m, reg, pol)
            ids = {row.id for q in spec["queries"] for row in run_query(StateQuery.from_dict(q), m).rows}
            self.assertEqual(set(ctx.decisions), ids, budget)
            handled = {i for h in ctx.handles.values() for i in h["ids"]}
            self.assertEqual(handled, {i for i, d in ctx.decisions.items() if d != KEEP})

    def test_must_rows_kept_even_over_budget(self):
        spec, m, reg, pol, *_ = world(budget=50)
        ctx = ctx_of(spec, m, reg, pol)
        self.assertIn("srv07", ctx.seen)       # critical
        self.assertIn("srv05", ctx.seen)       # 팬 고장
        self.assertTrue(ctx.over_budget)

    def test_three_actions_all_used(self):
        spec, m, reg, pol, *_ = world(budget=1500)
        st = ctx_of(spec, m, reg, pol).stats()
        self.assertGreater(st["keep"], 0)
        self.assertGreater(st["summarize"], 0)
        self.assertGreater(st["retrieve_only"], 0)

    def test_offers_only_for_seen(self):
        spec, m, reg, pol, *_ = world(budget=500)
        q = [{"name": "fleet", "model": "Server", "select": ["temp_c", "status", "fan"]}]   # must 없음
        ctx = ctx_of(spec, m, reg, pol, queries=q)
        raw = {t for o in tool_query(reg, [run_query(StateQuery.from_dict(q[0]), m)], m) for t in o.targets}
        self.assertTrue(raw - set(ctx.seen), "대조 실패: 안 본 개체 중에 도구를 쓸 수 있는 것이 없다")
        for o in ctx.offers:
            for t in o["targets"]:
                self.assertIn(t, ctx.seen)

    def test_retrieve_brings_rows_next_round(self):
        spec, m, reg, pol, arb, _ = world(budget=1500)
        first = ctx_of(spec, m, reg, pol)
        h = next(k for k, v in first.handles.items() if "srv09" in v["ids"])
        llm = ScriptedLLM([{"tool": "retrieve", "target": h}, {"tool": "none"}])
        res = Pipeline(m, reg, llm, pol, arb).run("t", spec["queries"])
        self.assertEqual(res.rounds[0]["decision"]["verdict"], ALLOW)
        self.assertIn('"srv09"', llm.prompts[1].split('"summaries"')[0])   # 둘째 판에는 state 칸에


# -- Arbiter ----------------------------------------------------------------
class ArbiterRules(unittest.TestCase):
    def setUp(self):
        self.spec, self.m, self.reg, self.pol, self.arb, self.clock = world(budget=1500)
        self.ctx = ctx_of(self.spec, self.m, self.reg, self.pol)

    def d(self, **kw):
        return self.arb.decide(P(**kw), self.ctx, self.m)

    def test_allow(self):
        d = self.d(tool="throttle", target="srv07", args={"level": 2})
        self.assertEqual((d.verdict, d.rule), (ALLOW, "0"))

    def test_A0_unparsable(self):
        self.assertEqual(self.arb.decide(parse_proposal("재부팅하세요"), self.ctx, self.m).rule, "A0")

    def test_A1_unoffered_tool(self):
        self.assertEqual(self.d(tool="format_disk", target="srv07").rule, "A1")

    def test_A2_never_seen(self):
        d = self.d(tool="throttle", target="srv99", args={"level": 1})
        self.assertEqual(d.rule, "A2")

    def test_A2_only_summarized(self):
        self.assertEqual(self.ctx.decisions.get("srv09"), SUMMARIZE)
        d = self.d(tool="throttle", target="srv09", args={"level": 1})
        self.assertEqual(d.rule, "A2")
        self.assertIn("retrieve", d.reasons[0])

    def test_A2_unknown_handle(self):
        self.assertEqual(self.d(tool="retrieve", target="h99").rule, "A2")

    def test_A3_seen_but_not_target(self):
        self.assertIn("srv01", self.ctx.seen)          # 봤지만 normal 이라 throttle 대상이 아니다
        self.assertEqual(self.d(tool="throttle", target="srv01", args={"level": 1}).rule, "A3")

    def test_A4_bad_args(self):
        self.assertEqual(self.d(tool="throttle", target="srv07", args={"level": 9}).rule, "A4")
        self.assertEqual(self.d(tool="throttle", target="srv07", args={}).rule, "A4")
        self.assertEqual(self.d(tool="throttle", target="srv07", args={"level": 1, "x": 1}).rule, "A4")

    def test_A5_state_changed_after_context(self):
        self.m.ingest(Telemetry("bmc", "srv07", "cpu_temp", 70, ts=999.5))   # 맥락을 지은 뒤 식었다
        d = self.d(tool="throttle", target="srv07", args={"level": 2})
        self.assertEqual(d.rule, "A5")

    def test_A6_stale_precondition(self):
        self.clock.t += 120                                                  # temp_c ttl 60 을 넘긴다
        self.assertEqual(self.d(tool="throttle", target="srv07", args={"level": 2}).rule, "A6")

    def test_A7_irreversible_needs_grant(self):
        self.assertEqual(self.d(tool="reboot", target="srv07").rule, "A7")
        self.assertEqual(self.d(tool="open_ticket", target="srv05", args={"note": "팬"}).rule, "A7")
        arb = Arbiter(self.reg, {"reboot"}, clock=self.clock)
        self.assertEqual(arb.decide(P(tool="reboot", target="srv07"), self.ctx, self.m).verdict, ALLOW)

    def test_A8_duplicate(self):
        self.assertEqual(self.d(tool="throttle", target="srv07", args={"level": 2}).verdict, ALLOW)
        self.assertEqual(self.d(tool="throttle", target="srv07", args={"level": 2}).rule, "A8")

    def test_exception_fails_closed(self):
        d = self.arb.decide(P(tool="throttle", target="srv07", args={"level": 2}), self.ctx, None)
        self.assertEqual((d.verdict, d.rule), (DENY, "E"))

    def test_noop(self):
        self.assertEqual(self.d(tool="none").verdict, NOOP)

    def test_ledger_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "l.jsonl")
            arb = Arbiter(self.reg, ledger_path=path, clock=self.clock)
            arb.decide(P(tool="reboot", target="srv07"), self.ctx, self.m)
            with open(path, encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertEqual((rec["verdict"], rec["rule"]), (DENY, "A7"))

    def test_deny_reason_goes_back_to_llm(self):
        llm = ScriptedLLM([{"tool": "reboot", "target": "srv07"}, {"tool": "none"}])
        Pipeline(self.m, self.reg, llm, self.pol, self.arb).run("t", self.spec["queries"])
        self.assertIn('"denied"', llm.prompts[1])
        self.assertIn("A7", llm.prompts[1])


class Parse(unittest.TestCase):
    def test_fenced_and_prose(self):
        p = parse_proposal('좋아요.\n```json\n{"tool": "throttle", "target": "srv07", "args": {"level": 2}}\n```')
        self.assertEqual((p.tool, p.target, p.args), ("throttle", "srv07", {"level": 2}))

    def test_bad_shapes(self):
        self.assertTrue(parse_proposal('{"tool": 3}').error)
        self.assertTrue(parse_proposal('{"tool": "x"}').error)
        self.assertTrue(parse_proposal('{"tool": "x", "target": "y", "args": [1]}').error)
        self.assertFalse(parse_proposal('{"tool": "none"}').error)


class Cli(unittest.TestCase):
    def run_ms(self, *args):
        return subprocess.run([sys.executable, "-m", "ms", *args], cwd=ROOT, capture_output=True, text=True,
                              timeout=60)

    def test_demo(self):
        p = self.run_ms("demo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DENY [A2]", p.stdout)
        self.assertIn("DENY [A7]", p.stdout)
        self.assertIn("ALLOW [0]", p.stdout)

    def test_ingest_json(self):
        p = self.run_ms("ingest", SPEC, "--telemetry", TELE, "--json")
        d = json.loads(p.stdout)
        self.assertEqual(d["counts"], {"applied": 25, "rejected": 1, "unbound": 2, "stale": 1})

    def test_context_json(self):
        p = self.run_ms("context", SPEC, "--telemetry", TELE, "--task", "t", "--json", "--budget", "1500")
        d = json.loads(p.stdout)
        self.assertLessEqual(d["stats"]["chars"], 1500)


if __name__ == "__main__":
    unittest.main()
