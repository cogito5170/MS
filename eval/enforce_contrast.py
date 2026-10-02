"""CMD-M24 끝난 기준 -- Guard enforce 대조: enforce 에서 실행된 집합 = {Arbiter ALLOW ∧ Guard ALLOW}, 막힌 것은 실행기 · L0 action.* ·
VERIFY 가 0, snapshot 길 실행 0, 나머지는 shadow 와 실행 · 관측이 같다.

    MS_DC_PATH=../DC TELEMETRY_REPO=../Telemetry MS_HEALTH_PATH=../health PYTHONPATH=../action:../guard \
        python3 eval/enforce_contrast.py [out.json]

같은 일을 프로세스를 따로 띄워 두 번 돌린다: shadow(지금 기본) · enforce(`Runtime(guard_mode=...)` 의 기본을 바꿔 끼운다).
일은 둘 -- MS 시험 전체 · 모의 평가 DC 길(묶음 둘 · 모든 칸 · 반복 2 · 씨앗 3). L0 는 실행마다 Telemetry Recorder 로 받는다
(시험이 자기 L0 를 준 실행은 그대로 둔다). 짝은 (시험 id, 몇 번째 실행) · 모의는 차례. 일마다 관측 id 셈을 처음부터 한다
(sensor_contrast 와 같다 -- 결정 id 가 그 셈에 묶인다).

enforce 쪽에서 실행마다 보는 것(그 자체로 서는 성질):
- 실행됨 ⇔ (도구 판의 Arbiter ALLOW ∧ Guard ALLOW · SAFE_ACTION) -- DC 길
- 막힘(guard_blocked)이면 실행기 기록 0 · L0 action.* 0 · tool.* 0 · VERIFY 0
- snapshot 길 실행 0
shadow 와 견주는 것: 같은 짝에서 enforce 가 실행했으면 실행 · 관측이 shadow 와 같다. 결정 id 는 견주지 않고 수만 적는다 --
막힌 실행은 관측을 넣지 않아 프로세스 전역 관측 id 셈(BD-104 의 3)이 그 뒤로 어긋나고, DC provenance(따라서 결정 id)가 달라진다.
시험 안에서는 enforce 로 바꿔 끼워 실패하는 시험이 많다(snapshot 길 실행을 기대하는 시험) -- 하니스가 수를 찍는다.
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
sys.path.insert(0, os.path.join(ROOT, "eval"))
from sensor_contrast import KEY, _paths, fresh_ids  # noqa: E402


def one_pass(mode: str) -> list:
    _paths()
    for env, name in (("MS_GUARD_PATH", "guard"), ("MS_HEALTH_PATH", "health")):
        p = os.environ.get(env, os.path.join(ROOT, "..", name))
        if os.path.isdir(os.path.join(p, name)) and p not in sys.path:
            sys.path.append(p)
    from unittest import mock
    from telemetry.hashing import Hasher
    from telemetry.ledger import MemorySink
    from telemetry.recorder import Recorder
    import ms.l0 as L
    import ms.runtime as R
    from ms import intent

    sinks, rows = {}, []
    current = {"key": "?", "n": 0}
    real_rec, real_init, orig = L.recorder, R.Runtime.__init__, R.Runtime.handle

    def recorder(run_id, ledger_path=None, sink=None, source="inproc:ms"):
        if ledger_path is not None or sink is not None:
            return real_rec(run_id, ledger_path, sink, source)
        sinks[run_id] = MemorySink()
        return Recorder(run_id, sinks[run_id], source=source, hasher=Hasher(KEY))

    def init(self, *a, **kw):
        kw.setdefault("guard_mode", mode)
        real_init(self, *a, **kw)

    def handle(self, request):
        out = orig(self, request)
        ev = sinks.pop(out["run_id"], None)
        current["n"] += 1
        g = {x["round"]: x for x in out.get("guards", [])}
        rounds = out["result"]["rounds"]
        tool_round = next((r for r in rounds if r.get("decision", {}).get("verdict") == "ALLOW"
                           and r.get("proposal", {}).get("tool") not in ("retrieve", "none")), None)
        gr = g.get(tool_round["round"], {}).get("guard", {}) if tool_round else {}
        gv = gr.get("verdict")
        rows.append({"key": f"{current['key']}#{current['n']}", "decision": out["decision"]["id"], "mode": self.guard_mode,
                     "dc": bool(intent.dc_id(out["decision"]["state_source"])),
                     "arbiter_allow": tool_round is not None, "guard": gv, "guard_rule": gr.get("rule"),
                     "real_dc": "reuse_key" in out["decision"]["state_source"],
                     "blocked": next((r["guard_blocked"] for r in rounds if "guard_blocked" in r), None),
                     "executed": out["result"]["executed"], "ingested": out["result"]["ingested"],
                     "executions": len(out.get("executions", [])), "verifications": len(out.get("verifications", [])),
                     "l0": [e["type"] for e in ev.events] if ev is not None else None})
        return out

    patches = [mock.patch.object(L, "recorder", recorder), mock.patch.object(R.Runtime, "__init__", init),
               mock.patch.object(R.Runtime, "handle", handle)]
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
        from ms.eval import CONFIGS, evaluate
        for t in ("eval/tasks/datacenter.json", "eval/tasks/datacenter_tasks3.json"):
            fresh_ids()
            current.update(key=f"sim:{t}", n=0)
            evaluate(os.path.join(ROOT, t), {"openai": "sim-openai", "claude": "sim-claude"}, configs=tuple(CONFIGS),
                     reps=2, seed=3, log=lambda *a: None, state_reader="dc")
    finally:
        for p in patches:
            p.stop()
    for r in rows:
        r["source"] = "sim" if r["key"].startswith("sim:") else "tests"
    return rows


def check_enforce(rows) -> dict:
    t, bad = collections.Counter(), []
    for r in rows:
        if r["mode"] != "enforce":                    # 시험이 shadow 를 직접 고른 실행(대조용)은 enforce 성질의 대상이 아니다
            t["시험이 고른 shadow 실행(제외)"] += 1
            continue
        ran = bool(r["executed"])
        if not r["dc"]:
            t["snapshot 실행"] += 1
            t["snapshot 도구 실행"] += ran
            if ran:
                bad.append(("snapshot 길에서 실행", r["key"]))
            continue
        t["DC 실행"] += 1
        want = r["arbiter_allow"] and r["guard"] in ("ALLOW", "SAFE_ACTION")
        if want and not ran and "실행기가" in (r["blocked"] or ""):     # 실행기가 받지 않을 명령 -- 지금 길로 안 가고 막음(시험이 처리기를 뗌)
            t["DC 둘 다 ALLOW · 실행기가 받지 않아 막음"] += 1
            want = False
        rule = f"({r['guard_rule']}{'' if r['real_dc'] else ', 가짜 리더'})" if r["guard"] == "DENY" else ""
        t[f"DC Arbiter {'ALLOW' if r['arbiter_allow'] else '-'} · Guard {r['guard']}{rule} → {'실행' if ran else '안 함'}"] += 1
        if ran != want:
            bad.append(("실행 ≠ Arbiter ALLOW ∧ Guard ALLOW", r["key"]))
        if r["blocked"]:
            t["DC 막힘"] += 1
            leak = (r["executions"], r["verifications"],
                    len([x for x in (r["l0"] or []) if x.startswith(("action.", "tool."))]))
            if any(leak):
                bad.append((f"막혔는데 실행기 · VERIFY · L0 {leak}", r["key"]))
    return {"table": dict(sorted(t.items())), "violations": bad[:20], "n_violations": len(bad)}


def compare(shadow, enforce) -> dict:
    S, E = {r["key"]: r for r in shadow}, {r["key"]: r for r in enforce}
    t, bad = collections.Counter(), []
    for k in sorted(set(S) & set(E)):
        s, e = S[k], E[k]
        if not e["dc"]:
            continue
        t["결정 id 같음" if s["decision"] == e["decision"] else "결정 id 다름"] += 1
        if e["mode"] != "enforce":
            continue
        if e["executed"]:
            same = (s["executed"], s["ingested"]) == (e["executed"], e["ingested"])
            t[f"enforce 실행 · shadow 와 {'같음' if same else '다름'}"] += 1
            if not same:
                bad.append(k)
        elif s["executed"]:
            t["shadow 는 실행 · enforce 는 막음"] += 1
    return {"table": dict(sorted(t.items())), "differ": bad[:20],
            "one_side": {"shadow_only": len(set(S) - set(E)), "enforce_only": len(set(E) - set(S))}}


def main(argv) -> int:
    if len(argv) > 2 and argv[1] == "--pass":
        json.dump(one_pass(argv[2]), sys.stdout, ensure_ascii=False)
        return 0
    got = {}
    for mode in ("shadow", "enforce"):
        r = subprocess.run([sys.executable, __file__, "--pass", mode], capture_output=True, text=True, cwd=ROOT)
        sys.stderr.write(r.stderr[-1500:])
        if r.returncode:
            raise SystemExit(f"{mode} 이 실패했다")
        got[mode] = json.loads(r.stdout)
    rep = {}
    for src in ("tests", "sim"):
        sh = [r for r in got["shadow"] if r["source"] == src]
        en = [r for r in got["enforce"] if r["source"] == src]
        rep[src] = {"enforce": check_enforce(en), "vs_shadow": compare(sh, en)}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    bad = sum(v["enforce"]["n_violations"] + len(v["vs_shadow"]["differ"]) for v in rep.values())
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
