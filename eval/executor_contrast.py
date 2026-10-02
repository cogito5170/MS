"""CMD-M20 끝난 기준 -- DC 길 실행에서 실행기(shadow)의 `would_dispatch` 대 실제 실행의 대조표.

    MS_DC_PATH=../DC MS_ACTION_PATH=../action MS_GUARD_PATH=../guard python3 eval/executor_contrast.py [out.json]

모으는 곳 둘
- `tests`: MS 시험 전체를 이 프로세스에서 돌리며 DC 길(결정 문맥 id 가 있는) 실행을 모은다(Action 이 잰 43 번 + 그 뒤 더한 시험).
  진짜 DC 문맥(`tests_real_dc`, state_source 에 DC 의 reuse_key 가 있다)과 시험의 가짜 리더(`tests_fake_reader`, id 만 준다)로도 가른다 --
  가짜 리더에는 Guard 가 DCView 를 지을 재료가 없어 E 가 난다.
- `sim`: 모의 평가 DC 길(묶음 둘 · 모든 칸 · 반복 2 · 씨앗 3).

견주는 것: 도구 이름 · 겨냥 · 결정 id. 실제 쪽은 훅이 아니라 handle 결과에서 **따로** 읽는다 -- `result.executed` 와
`decision.id`. 실행기 쪽은 `executions[].execution.would_dispatch`(action_type · target · decision_ref).
- 같음 / 다름(셋 중 하나라도) / 명령 없음(실행했는데 명령이 없다 -- 까닭별) / 남음(명령은 있는데 실행이 없다).
Runtime.handle 을 감싸 모은다(평가 도구).
"""
from __future__ import annotations

import collections
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for env, name in (("MS_ACTION_PATH", "action"), ("MS_GUARD_PATH", "guard")):
    p = os.environ.get(env, os.path.join(ROOT, "..", name))
    if os.path.isdir(os.path.join(p, name)) and p not in sys.path:
        sys.path.append(p)               # 뒤에 -- 옆 저장소의 tests 패키지가 MS 의 tests 를 가리지 않게

import ms.runtime as R  # noqa: E402
from ms import executor_shadow, intent  # noqa: E402


def _collect(run) -> list:
    rows = []
    orig = R.Runtime.handle

    def handle(self, request):
        out = orig(self, request)
        if intent.dc_id(out["decision"]["state_source"]):
            rows.append({"executed": out["result"]["executed"], "decision": out["decision"]["id"],
                         "real_dc": "reuse_key" in out["decision"]["state_source"],  # 진짜 DC MSStateReader 의 기록 · 시험의 가짜 리더
                         "executions": out.get("executions", []), "dispatch_on": self.dispatch is not None})
        return out
    R.Runtime.handle = handle
    try:
        run()
    finally:
        R.Runtime.handle = orig
    return rows


def _tally(rows) -> dict:
    t = collections.Counter()
    why, diffs = collections.Counter(), []
    for r in rows:
        if not r["dispatch_on"]:
            t["shadow 꺼짐(시험이 끈 것)"] += 1
            continue
        actual = [(e["tool"], e["target"], r["decision"]) for e in r["executed"]]
        disp = [(x["execution"]["would_dispatch"]["action_type"], x["execution"]["would_dispatch"]["target"],
                 x["execution"]["would_dispatch"]["decision_ref"]) for x in r["executions"] if x.get("execution")]
        for x in r["executions"]:
            if not x.get("execution"):
                why[x.get("error", "?")] += 1
        if not actual and not disp:
            t["실행 없음 · 명령 없음"] += 1
        elif actual and not disp:
            t["명령 없음"] += 1
        elif disp and not actual:
            t["남음"] += 1
            diffs.append({"actual": actual, "dispatch": disp})
        elif actual == disp:
            t["같음"] += 1
        else:
            t["다름"] += 1
            diffs.append({"actual": actual, "dispatch": disp})
    return {"dc_runs": len(rows), "executed": sum(1 for r in rows if r["executed"]), "table": dict(t),
            "no_command_why": dict(why), "diffs": diffs[:20]}


def tests_rows() -> list:
    def run():
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
        res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        print(f"시험: {res.testsRun} 돌림 · 실패 {len(res.failures)} · 오류 {len(res.errors)} · 건너뜀 {len(res.skipped)}",
              file=sys.stderr)
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        return _collect(run)
    finally:
        os.chdir(cwd)


def sim_rows() -> list:
    from ms.eval import CONFIGS, evaluate

    def run():
        for t in ("eval/tasks/datacenter.json", "eval/tasks/datacenter_tasks3.json"):
            evaluate(os.path.join(ROOT, t), {"openai": "sim-openai", "claude": "sim-claude"}, configs=tuple(CONFIGS),
                     reps=2, seed=3, log=lambda *a: None, state_reader="dc")
    return _collect(run)


def main(argv) -> int:
    if not executor_shadow.available():
        raise SystemExit("action · guard 를 못 읽는다 -- MS_ACTION_PATH · MS_GUARD_PATH")
    t = tests_rows()
    rep = {"tests": _tally(t), "tests_real_dc": _tally([r for r in t if r["real_dc"]]),
           "tests_fake_reader": _tally([r for r in t if not r["real_dc"]]), "sim": _tally(sim_rows())}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    bad = sum(rep[k]["table"].get("다름", 0) + rep[k]["table"].get("남음", 0) for k in ("tests", "sim"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
