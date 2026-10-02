"""MS 런타임 → L0 Telemetry(선택 의존). "무슨 일이 일어났나" 만 L0 로 가고, 결정의 내용은 가지 않는다."""
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

from ms import l0
from ms.providers import CallableProvider, make_provider
from ms.providers.base import ProviderError
from ms.runtime import Runtime
from tests.test_ms import world

ROOT = pathlib.Path(__file__).resolve().parents[1]
TELEMETRY = ROOT.parent / "Telemetry"
SENSOR = ROOT.parent / "Sensor"
TASK = "srv07 을 throttle"


class Boom(CallableProvider):
    """provider 가 HTTP 429 로 거절한 것처럼."""
    name = "claude"

    def __init__(self):
        super().__init__(lambda p: "")
        self.name = "claude"

    def generate(self, req):
        raise ProviderError("HTTP 429: secret-body", 429, {"error": {"type": "rate_limit_error"}}, {"retry-after": "3"})


class WithoutL0(unittest.TestCase):
    def test_runtime_runs_and_emits_nothing_without_l0(self):
        spec, m, reg, *_ = world()
        with mock.patch.dict(sys.modules, {"telemetry": None}):
            self.assertFalse(l0.available())
            rt = Runtime(m, reg, {"sim-claude": make_provider("sim-claude")})
            rt.open_session("s", {"token_budget": 1000})
            out = rt.handle({"session": "s", "task": TASK, "queries": spec["queries"]})
            self.assertTrue(out["record"]["decision_ref"])
            with self.assertRaises(ImportError):            # 달라고 했는데 없으면 조용히 버리지 않는다
                Runtime(m, reg, {"sim-claude": make_provider("sim-claude")}, l0_ledger="x.jsonl")

    def test_impostor_named_telemetry_is_not_l0(self):
        with mock.patch.dict(sys.modules, {"telemetry": types.ModuleType("telemetry")}):
            self.assertFalse(l0.available())


@unittest.skipUnless((TELEMETRY / "telemetry").is_dir(), "옆에 ../Telemetry 가 없다")
class WithL0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(TELEMETRY) not in sys.path:
            sys.path.insert(0, str(TELEMETRY))
        from telemetry import MemorySink, check
        cls.MemorySink, cls.check = MemorySink, staticmethod(check)

    def run_once(self, providers=None, tool_handler=None):
        spec, m, reg, *_ = world()
        if tool_handler is not None:
            reg.get("throttle").handler = tool_handler
        sink = self.MemorySink()
        rt = Runtime(m, reg, providers or {"sim-claude": make_provider("sim-claude")}, l0_sink=sink)
        rt.open_session("s", {"token_budget": 1000})
        out = rt.handle({"session": "s", "task": TASK, "queries": spec["queries"]})
        return out, sink.events

    def test_events_are_well_formed_and_tell_what_happened(self):
        out, evs = self.run_once()
        self.assertEqual([self.check(e) for e in evs], [[]] * len(evs))
        types_ = [e["type"] for e in evs]
        self.assertTrue(out["result"]["executed"])                        # 도구가 실제로 불린 실행이다(대조가 헛돌지 않게)
        self.assertEqual(types_[0], "run.start")
        self.assertEqual(types_[-1], "run.end")
        self.assertEqual(types_.count("llm.request"), len(out["result"]["calls"]))
        self.assertEqual(types_.count("llm.response"), len(out["result"]["calls"]))
        self.assertEqual(types_.count("tool.start"), len(out["result"]["executed"]))
        self.assertEqual(types_.count("tool.end"), len(out["result"]["executed"]))
        self.assertEqual({e["run_id"] for e in evs}, {out["run_id"]})          # 한 실행 = 한 run_id
        self.assertEqual({e["data"]["provider"] for e in evs if e["type"].startswith("llm.")},
                         {"sim-claude"})                                    # 모의 실행이 진짜 claude 로 안 보인다
        self.assertEqual([e["seq"] for e in evs], list(range(len(evs))))

    def test_run_end_links_decision_by_id_only(self):
        out, evs = self.run_once()
        end = evs[-1]["data"]
        self.assertEqual(end["decision_ref"], out["decision"]["id"])
        self.assertEqual(end["decision_ref"], out["record"]["decision_ref"])
        self.assertEqual((end["terminal_reason"], end["num_turns"]),
                         (out["result"]["outcome"], len(out["result"]["rounds"])))
        blob = json.dumps(evs, ensure_ascii=False)
        for word in ("token_budget_pressure", "context_pressure", "ALLOW", "DENY", "arbiter", "verdict",
                     "context_policy", "srv07"):                                # 결정 · 상태 · 겨냥 글이 L0 에 없다
            self.assertNotIn(word, blob)

    def test_usage_matches_run_record_without_inventing_a_split(self):
        out, evs = self.run_once()
        resp = [e for e in evs if e["type"] == "llm.response"]
        rec = out["record"]["tokens"]
        self.assertEqual(sum(e["data"]["total_input_tokens"] for e in resp), rec["input_tokens"])
        self.assertEqual(sum(e["data"]["output_tokens"] for e in resp), rec["output_tokens"])
        for e in resp:
            self.assertIn("input_tokens", e["unobserved"])           # 캐시 밖 입력은 MS canonical 에서 셀 수 없다
            self.assertIsNotNone(e["data"]["elapsed_ms"])
        self.assertIn("cost_usd", evs[-1]["unobserved"])               # 모의 provider -- 가격표 비용은 L0 가 아니다

    def test_price_table_cost_stays_out_of_l0(self):
        """가격표로 계산한 비용은 해석(단가 가정)이다 -- RunRecord 에는 있어도 L0 run.end 에는 없다."""
        spec, m, reg, *_ = world()
        prov = make_provider("sim-claude")
        sink = self.MemorySink()
        rt = Runtime(m, reg, {"sim-claude": prov}, l0_sink=sink,
                     prices={prov.model: {"input": 3.0, "output": 15.0}})
        rt.open_session("s", {"token_budget": 1000})
        out = rt.handle({"session": "s", "task": TASK, "queries": spec["queries"]})
        self.assertEqual(out["record"]["cost"]["source"], "price_table")
        self.assertIsNotNone(out["record"]["cost"]["usd"])
        self.assertIn("cost_usd", sink.events[-1]["unobserved"])

    def test_tool_error_is_observed_and_message_dropped(self):
        def boom(target, args):
            raise RuntimeError("secret-path /etc/x")
        out, evs = self.run_once(tool_handler=boom)
        self.assertEqual(out["result"]["outcome"], "executed")       # 런타임 동작은 그대로
        end = [e for e in evs if e["type"] == "tool.end"][0]["data"]
        self.assertEqual((end["is_error"], end["exception"]), (True, "RuntimeError"))
        self.assertIsNotNone(end["elapsed_ms"])
        self.assertNotIn("secret", json.dumps(evs))

    def test_provider_error_becomes_llm_error(self):
        out, evs = self.run_once(providers={"claude": Boom()})
        self.assertEqual(out["result"]["outcome"], "llm_error")
        err = [e for e in evs if e["type"] == "llm.error"][0]["data"]
        self.assertEqual((err["http_status"], err["provider_code"], err["error_code"], err["retry_after_ms"],
                          err["exception"]), (429, "rate_limit_error", "RATE_LIMITED", 3000, "ProviderError"))
        self.assertFalse([e for e in evs if e["type"] == "llm.response"])
        self.assertEqual(evs[-1]["data"]["terminal_reason"], "llm_error")
        self.assertNotIn("secret", json.dumps(evs))

    def test_ledger_file(self):
        from telemetry import ledger
        spec, m, reg, *_ = world()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l0.jsonl")
            rt = Runtime(m, reg, {"sim-claude": make_provider("sim-claude")}, l0_ledger=p)
            rt.open_session("s", {"token_budget": 1000})
            for _ in range(2):
                rt.handle({"session": "s", "task": TASK, "queries": spec["queries"]})
            evs = ledger.read(p)                                       # 꼴 검사를 거쳐 읽힌다
        self.assertEqual(len({e["run_id"] for e in evs}), 2)

    @unittest.skipUnless((SENSOR / "llmsensor").is_dir(), "옆에 ../Sensor 가 없다")
    def test_sensor_reads_ms_ledger(self):
        """L0 → Sensor 꼴 v3 → Sensor State 정규화. MS 실행이 Sensor 가 읽는 관측이 된다(닫힌 고리의 아래쪽)."""
        if str(SENSOR) not in sys.path:
            sys.path.insert(0, str(SENSOR))
        from llmsensor.state.normalize import from_telemetry
        from llmsensor.telemetry.schema import check as v4check
        from telemetry.compat import to_sensor_records
        _, evs = self.run_once()
        recs = to_sensor_records(evs)
        self.assertEqual([v4check(r) for r in recs], [[]] * len(recs))      # inproc:ms 도 Sensor 꼴을 통과(CMD-T3)
        fields = {o.field for b in from_telemetry(recs) for o in b.observations}
        self.assertLessEqual({"tokens.output", "tool.is_error", "tool.name", "call.stop_reason"}, fields)


if __name__ == "__main__":
    unittest.main()
