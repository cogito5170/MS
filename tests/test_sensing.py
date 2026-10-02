"""② Sensor → Telemetry 계약 시험. 픽스처는 진짜 llmsensor(d4b80b3)의 sense() 출력이고, 날글(비밀)을 일부러 심었다."""
import ast
import copy
import json
import os
import unittest

from ms.manager import UNBOUND, StateManager
from ms.sensing import CONTRACT, REFUSED, STATUSES, ingest, otel_events, to_telemetry
from tests.test_ms import ROOT, Clock

FIX = os.path.join(ROOT, "tests", "fixtures")
SECRETS = ("SECRET_STDOUT_TOKEN", "SECRET_TOOL_OUTPUT", "SECRET_PROMPT_TEXT", "SECRET_ANSWER_VALUE", "/secret/path",
           "test_secret.py", "pytest", "never_written")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class Contract(unittest.TestCase):
    def setUp(self):
        self.fault, self.ok = load("sense_fault.json"), load("sense_ok.json")

    def test_fixture_really_holds_secrets(self):              # 대조 -- 아래 시험이 헛돌지 않는다
        raw = json.dumps(self.fault, ensure_ascii=False)
        self.assertIn("SECRET_STDOUT_TOKEN", raw)
        self.assertIn("/secret/path", raw)

    def test_no_raw_text_crosses(self):
        for data in (self.fault, self.ok):
            out = json.dumps(to_telemetry(data, "session:s", 1.0).to_dict(), ensure_ascii=False)
            for s in SECRETS:
                self.assertNotIn(s, out, s)
            for rd in data["readings"]:                          # why 글도 하나도 안 넘어간다
                if rd.get("why"):
                    self.assertNotIn(rd["why"], out)

    def test_only_numbers_bools_and_statuses(self):
        for s in to_telemetry(self.fault, "e", 1.0).signals:
            v = s["value"]
            if s["signal"].endswith(".status"):
                self.assertIn(v, STATUSES)
            else:
                self.assertTrue(isinstance(v, (int, float)) and not isinstance(v, str), s)

    def test_estimates_and_decisions_refused(self):
        r = to_telemetry(self.fault, "e", 1.0)
        self.assertEqual(sorted(r.refused), sorted(REFUSED))
        names = [s["signal"] for s in r.signals]
        self.assertFalse([n for n in names if "fusion" in n or "verdict" in n or n.endswith(".Q")])
        self.assertNotIn(json.dumps(self.fault["verdict"]["action"]), json.dumps(r.signals))

    def test_unknown_stays_unknown(self):
        sig = {s["signal"]: s["value"] for s in to_telemetry(self.fault, "e", 1.0).signals}
        self.assertEqual(sig["sensor.consistency.numbers.status"], "UNKNOWN")
        self.assertNotIn("sensor.consistency.numbers.value", sig)     # 값이 없으면 신호도 없다

    def test_measurements_pass_through(self):
        sig = {s["signal"]: s["value"] for s in to_telemetry(self.fault, "e", 1.0).signals}
        self.assertEqual(sig["sensor.tel.T"], self.fault["telemetry"]["T"])
        self.assertEqual(sig["sensor.execution.status"], "FAULT")
        self.assertEqual(sig["sensor.execution.written_but_missing"], 1)      # 경로 목록 -> 길이
        self.assertEqual(sig["sensor.consistency.claim.last_ok"], False)
        self.assertEqual(sig["sensor.outcome.returncode"], 1)

    def test_new_fields_are_dropped_by_name(self):
        data = copy.deepcopy(self.fault)
        data["readings"][0]["detail"]["new_text"] = "SECRET_NEW_FIELD"
        data["readings"][0]["detail"]["new_number"] = 7
        data["telemetry"]["new_tel"] = 3
        data["shiny_new_section"] = {"x": "SECRET_NEW_SECTION"}
        r = to_telemetry(data, "e", 1.0)
        out = json.dumps(r.to_dict(), ensure_ascii=False)
        self.assertNotIn("SECRET_NEW", out)
        self.assertNotIn("new_number", json.dumps(r.signals))               # 수여도 화이트리스트 밖이면 안 받는다
        for n in ("execution.new_text", "execution.new_number", "tel.new_tel", "shiny_new_section"):
            self.assertIn(n, r.dropped)

    def test_bad_types_dropped(self):
        data = copy.deepcopy(self.fault)
        data["telemetry"]["T"] = "1500"                                      # 글로 된 수
        data["readings"][0]["status"] = "GREAT"                              # 모르는 상태
        data["readings"][0]["value"] = float("nan")
        sig = {s["signal"]: s["value"] for s in to_telemetry(data, "e", 1.0).signals}
        self.assertNotIn("sensor.tel.T", sig)
        self.assertNotIn("sensor.execution.status", sig)
        self.assertNotIn("sensor.execution.value", sig)

    def test_json_text_equals_dict(self):
        a = to_telemetry(self.fault, "e", 1.0).to_dict()
        b = to_telemetry(json.dumps(self.fault, ensure_ascii=False), "e", 1.0).to_dict()
        self.assertEqual(a, b)
        self.assertEqual(a["contract"], CONTRACT)


class TelemetryIsNotState(unittest.TestCase):
    def test_without_a_model_everything_is_unbound(self):
        m = StateManager(clock=Clock())
        m.add_model({"name": "Session", "properties": {}})
        m.declare("session:s", "Session")
        before = m.graph.dump()
        out = ingest(m, load("sense_fault.json"), "session:s")
        self.assertEqual(out["ingest"], {UNBOUND: out["signals"]})          # ③ 의 모형이 없으면 상태가 아니다
        self.assertEqual(m.graph.dump(), before)
        refused = [q for q in m.quarantine if q["status"] == "refused"]
        self.assertEqual(sorted(q["signal"] for q in refused), ["sensor:fusion", "sensor:verdict"])
        self.assertNotIn("SECRET", json.dumps(list(m.quarantine), ensure_ascii=False))
        self.assertNotIn("RETRY", json.dumps(list(m.quarantine), ensure_ascii=False))


class Otel(unittest.TestCase):
    def test_evaluation_result_shape(self):
        ev = otel_events(load("sense_fault.json"))
        names = {e["attributes"]["gen_ai.evaluation.name"] for e in ev}
        self.assertLessEqual({"execution", "constraint", "consistency", "behavior", "outcome", "behavior.loop"}, names)
        for e in ev:
            self.assertEqual(e["name"], "gen_ai.evaluation.result")
            self.assertIn(e["attributes"]["gen_ai.evaluation.score.label"], STATUSES)
            self.assertNotIn("gen_ai.evaluation.explanation", e["attributes"])   # why 를 버렸다


class Isolation(unittest.TestCase):
    def test_ms_does_not_import_sensor(self):
        for dp, _, fs in os.walk(os.path.join(ROOT, "ms")):
            for f in fs:
                if f.endswith(".py"):
                    with open(os.path.join(dp, f), encoding="utf-8") as fh:
                        tree = ast.parse(fh.read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            self.assertFalse(any(a.name.split(".")[0] == "llmsensor" for a in node.names), f)
                        if isinstance(node, ast.ImportFrom) and node.level == 0:
                            self.assertNotEqual((node.module or "").split(".")[0], "llmsensor", f)


class LiveSensor(unittest.TestCase):
    """llmsensor 가 깔려 있으면 진짜 sense() 출력으로 계약을 다시 본다(없으면 건너뛴다)."""

    def test_live_contract(self):
        try:
            from llmsensor import Recorder, sense
        except ImportError:
            self.skipTest("llmsensor 없음(선택 의존)")
        rec = Recorder("과업 SECRET_PROMPT_TEXT", cls="x")
        rec.turn(input_tokens=10, output_tokens=2)
        rec.call("Bash", {"command": "echo hi"}, ok=True, output="SECRET_TOOL_OUTPUT")
        rec.answer("끝")
        r = to_telemetry(sense(rec.done()), "e", 1.0)
        self.assertEqual(sorted(r.refused), sorted(REFUSED))
        self.assertNotIn("SECRET", json.dumps(r.to_dict(), ensure_ascii=False))
        self.assertTrue(r.signals)
        unknown = [d for d in r.dropped if not d.startswith("res.")]
        self.assertEqual(unknown, [], "Sensor 가 화이트리스트 밖의 칸을 더했다 -- MS 쪽 화이트리스트를 보라")


if __name__ == "__main__":
    unittest.main()
