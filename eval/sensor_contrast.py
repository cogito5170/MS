"""CMD-M22 끝난 기준 -- DC 길을 실행기(execute)로 바꾸기 **전**(tool.*)과 **후**(action.*)의 L0 를 Sensor 에 넣어 견준다.

    MS_DC_PATH=../DC SENSOR_REPO=../Sensor TELEMETRY_REPO=../Telemetry PYTHONPATH=../action[:../guard] \
        python3 eval/sensor_contrast.py [out.json]

같은 일을 두 번 돌린다 -- 프로세스를 따로 띄운다(결정 id 가 프로세스 안 관측 id 셈에 묶이므로, 새 프로세스여야 두 번이 같다).
    before  실행기를 끈다(`ms.dispatch.available` → False). DC 길도 지금까지처럼 tool.run · tool.*
    after   지금 코드. DC 길은 execute(action.*), snapshot 길은 tool.*
일은 둘: MS 시험 전체(이 프로세스에서) · 모의 평가 DC 길(묶음 둘 · 모든 칸 · 반복 2 · 씨앗 3).

실행(run)마다 L0 를 Telemetry Recorder(고정 열쇠 Hasher)로 MemorySink 에 받는다. 시험이 자기 L0 원장 · sink 를 준 실행은
건드리지 않는다(그 실행은 모으지 않는다). 하니스 안에서는 L0 를 바꿔 끼우므로 시험 몇이 빨개진다(NullRecorder 확인 ·
벽시계 · telemetry 를 숨기는 시험) -- 하니스가 그 이름을 stderr 에 찍는다. 견주는 것(같은 차례의 실행끼리):
- 결정 id · ingest 된 관측
- Sensor(StateEngine): agent 실체의 `execution_health`(값 · 상태) · `identical_call_max` · `tool_errors`
- L0 사건 종류: DC 길은 후에 action.* 한 쌍 · tool.* 0, snapshot 길은 전과 같아야 한다
"""
from __future__ import annotations

import collections
import io
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = b"k" * 32


def _paths():
    sys.path.insert(0, ROOT)
    for env, d, pkg in (("TELEMETRY_REPO", "Telemetry", "telemetry"), ("SENSOR_REPO", "Sensor", "llmsensor")):
        p = os.environ.get(env, os.path.join(ROOT, "..", d))
        if os.path.isdir(os.path.join(p, pkg)) and p not in sys.path:
            sys.path.append(p)                       # 뒤에 -- 옆 저장소의 tests 패키지가 MS 의 tests 를 가리지 않게


def _sensor(events) -> dict:
    from llmsensor.sensing.l0 import batches
    from llmsensor.state import StateEngine, from_telemetry
    from telemetry.compat import to_sensor_records
    E = StateEngine()
    E.ingest_all(from_telemetry(to_sensor_records(events)))
    E.ingest_all(batches(events))
    run = events[0]["run_id"]

    def metric(name):
        ms = [m for i, m in E.metrics.items() if i.startswith(f"agent:{run}/{name}@")]
        return max(ms, key=lambda m: int(str(m.id.rsplit("@", 1)[1]).split("+")[0])).value if ms else None
    h = E.current.get((f"agent:{run}", "execution_health"))
    return {"execution_health": [h.value, str(h.status)] if h else None,
            "identical_call_max": metric("identical_call_max"), "tool_errors": metric("tool_errors")}


def fresh_ids():
    """관측 id 는 프로세스 전역 셈이고 DC provenance(따라서 결정 문맥 id · 결정 id)에 든다(BD-104 의 3). before 는 실행기 시험
    몇이 중간에 멈춰 셈이 어긋난다 -- 그래서 일(시험 · 묶음 · 대본)마다 처음부터 센다. 두 쪽이 같은 일을 같은 셈에서 시작한다."""
    import itertools
    import ms.telemetry
    ms.telemetry._ids = itertools.count(1)


def one_pass(mode: str) -> list:
    _paths()
    from unittest import mock
    from telemetry.hashing import Hasher
    from telemetry.ledger import MemorySink
    from telemetry.recorder import Recorder
    import ms.l0 as L
    import ms.runtime as R
    from ms import dispatch, intent

    sinks: dict = {}
    real_rec = L.recorder

    def recorder(run_id, ledger_path=None, sink=None, source="inproc:ms"):
        if ledger_path is not None or sink is not None:      # 시험이 자기 L0 를 준 실행은 그대로
            return real_rec(run_id, ledger_path, sink, source)
        sinks[run_id] = MemorySink()
        return Recorder(run_id, sinks[run_id], source=source, hasher=Hasher(KEY))

    rows = []
    current = {"key": "?", "n": 0}                   # 지금 도는 시험(짝짓기 열쇠). 시험이 실행 몇 번째인지도 센다
    orig = R.Runtime.handle

    def handle(self, request):
        out = orig(self, request)
        ev = sinks.pop(out["run_id"], None)
        if ev is not None:
            ev = list(ev.events)
            current["n"] += 1
            rows.append({"key": f"{current['key']}#{current['n']}", "decision": out["decision"]["id"], "dc": bool(intent.dc_id(out["decision"]["state_source"])),
                         "executed": out["result"]["executed"], "ingested": out["result"]["ingested"],
                         "l0": [e["type"] for e in ev], "sensor": _sensor(ev) if ev else None,
                         "fallback": [x["fallback"] for x in out.get("executions", []) if "fallback" in x]})
        return out

    patches = [mock.patch.object(L, "recorder", recorder), mock.patch.object(R.Runtime, "handle", handle)]
    if mode == "before":
        patches.append(mock.patch.object(dispatch, "available", lambda: False))
    for p in patches:
        p.start()
    try:
        os.chdir(ROOT)
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)

        class Result(unittest.TextTestResult):
            def startTest(self, test):
                current.update(key=test.id(), n=0)
                fresh_ids()
                super().startTest(test)
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0, resultclass=Result).run(suite)
        n_tests = len(rows)
        print(f"[{mode}] 시험 {res.testsRun} · 실패 {len(res.failures)} · 오류 {len(res.errors)} · 모은 실행 {n_tests}",
              file=sys.stderr)
        for t, tb in res.failures + res.errors:          # 이 하니스 안에서는 빨개지는 것이 있다(L0 를 바꿔 끼우고, before 는 실행기를 끈다)
            print(f"  {t.id().rsplit('.', 2)[-2]}.{t.id().rsplit('.', 1)[-1]}: {tb.strip().splitlines()[-1][:120]}",
                  file=sys.stderr)
        from ms.eval import CONFIGS, evaluate
        for t in ("eval/tasks/datacenter.json", "eval/tasks/datacenter_tasks3.json"):
            fresh_ids()
            evaluate(os.path.join(ROOT, t), {"openai": "sim-openai", "claude": "sim-claude"}, configs=tuple(CONFIGS),
                     reps=2, seed=3, log=lambda *a: None, state_reader="dc")
        n_sim = len(rows)
        scripted()
    finally:
        for p in patches:
            p.stop()
    for i, r in enumerate(rows):
        r["source"] = "tests" if i < n_tests else "sim" if i < n_sim else "scripted"
        if r["source"] != "tests":
            r["key"] = f"{r['source']}#{i - (n_tests if r['source'] == 'sim' else n_sim)}"
    return rows


FAULTS = {"정상": None,
          "처리기가 던짐": lambda target, args: (_ for _ in ()).throw(RuntimeError("팬이 멈췄다")),
          "tool_error 보고": lambda target, args: [{"entity": target, "signal": "tool_error", "value": "못 함"}]}


def scripted():
    """사소한 설명 가르기: 시험 · 모의의 DC 길 실행은 모두 실패 없음(NO_FAILURE_OBSERVED)이다. 그러면 Sensor 의 "같음" 은
    실패 없음끼리의 같음뿐이다. 그래서 DC 길에서 도구가 실패하는 실행(던짐 · tool_error 보고)을 일부러 돌린다."""
    import itertools
    from ms import StateManager, ToolRegistry
    from ms.eval import dc_state_reader
    from ms.llm import ScriptedLLM
    from ms.runtime import Runtime
    Reader, _ = dc_state_reader()
    ex = os.path.join(ROOT, "ms", "examples")
    with open(os.path.join(ex, "datacenter.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    for name, handler in FAULTS.items():
        fresh_ids()
        m = StateManager.from_spec(spec, clock=itertools.repeat(spec["now"]).__next__)
        with open(os.path.join(ex, "datacenter_telemetry.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    m.ingest(json.loads(line))
        reg = ToolRegistry(spec["tools"])
        if handler is not None:
            reg.get("throttle").handler = handler
        rt = Runtime(m, reg, {"p": ScriptedLLM([{"tool": "throttle", "target": "srv07", "args": {"level": 2}}])})
        rt.state_reader = Reader(rt.um, m)
        rt.open_session("s", {"token_budget": 100000})
        rt.handle({"session": "s", "task": name, "queries": spec["queries"], "max_rounds": 1})


def compare(before: list, after: list) -> dict:
    """짝은 (시험 id, 그 시험 안의 몇 번째 실행) -- 모의 · 일부러 낸 실행은 차례. before 는 실행기를 꺼서 실행기 시험 몇이
    중간에 멈춘다(그 시험의 실행은 한쪽에만 있다). 한쪽에만 있는 실행은 `one_side` 에 시험 이름과 함께 적는다."""
    B, A = {r["key"]: r for r in before}, {r["key"]: r for r in after}
    rep = {"runs": [len(before), len(after)], "paired": len(set(B) & set(A)), "by_source": {},
           "one_side": {"before_only": sorted(set(B) - set(A)), "after_only": sorted(set(A) - set(B))}}
    for src in ("tests", "sim", "scripted"):
        t = collections.Counter()
        diffs = []
        for b, a in ((B[k], A[k]) for k in sorted(set(B) & set(A)) if A[k]["source"] == src):
            path = "dc" if a["dc"] else "snapshot"
            t[f"{path} 실행"] += 1
            if a["executed"]:
                t[f"{path} 도구 실행"] += 1
            same = {"결정 id": b["decision"] == a["decision"], "관측": b["ingested"] == a["ingested"],
                    "Sensor": b["sensor"] == a["sensor"]}
            if a["fallback"]:                             # 실행기로 못 간 DC 실행(까닭별) -- 지금 길(tool.*) 그대로여야 한다
                t[f"dc 실행기 못 감: {', '.join(a['fallback'])}"] += 1
                same["L0"] = b["l0"] == a["l0"]
            elif path == "snapshot":
                same["L0"] = b["l0"] == a["l0"]
            else:
                n = len(a["executed"])
                same["L0"] = (a["l0"].count("action.dispatch") == a["l0"].count("action.result") == n
                              and not [x for x in a["l0"] if x.startswith("tool.")]
                              and [x for x in a["l0"] if not x.startswith("action.")]
                              == [x for x in b["l0"] if not x.startswith("tool.")])
            for k, v in same.items():
                t[f"{path} {k} {'같음' if v else '다름'}"] += 1
            if not all(same.values()):
                diffs.append({"path": path, "다름": [k for k, v in same.items() if not v], "before": b, "after": a})
        rep["by_source"][src] = {"table": dict(sorted(t.items())), "diffs": diffs[:10]}
    return rep


def main(argv) -> int:
    if len(argv) > 2 and argv[1] == "--pass":
        json.dump(one_pass(argv[2]), sys.stdout, ensure_ascii=False)
        return 0
    got = {}
    for mode in ("before", "after"):
        r = subprocess.run([sys.executable, __file__, "--pass", mode], capture_output=True, text=True, cwd=ROOT)
        sys.stderr.write(r.stderr[-2000:])
        if r.returncode:
            raise SystemExit(f"{mode} 이 실패했다")
        got[mode] = json.loads(r.stdout)
    rep = compare(got["before"], got["after"])
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:6000])
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    bad = sum(v for s in rep.get("by_source", {}).values() for k, v in s["table"].items() if k.endswith("다름"))
    lost = [k for k in rep["one_side"]["before_only"] + rep["one_side"]["after_only"]
            if ".ExecutorWiring." not in k]       # 한쪽에만 있어도 되는 것은 before 에서 실행기를 꺼서 멈춘 실행기 시험뿐
    return 1 if bad or lost else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
