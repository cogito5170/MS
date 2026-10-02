"""CMD-M17 끝난 기준 2 -- 모의 평가의 DC 길에서 Arbiter 와 Guard(shadow)의 판정 대조표.

    MS_DC_PATH=../DC MS_ACTION_PATH=../action MS_GUARD_PATH=../guard python3 eval/guard_contrast.py [out.json]

모의 provider(sim-openai · sim-claude)로 과업 묶음 둘(datacenter · datacenter-tasks-3)을 모든 칸 · 반복 2 · 씨앗 3 으로 돌린다
(`--state-reader dc` 와 같은 길). 실행은 Arbiter 가 정한다. Guard 는 의도(DC 배선)마다 판정을 기록만 한다.

센다
- 같음: (verdict, rule) 가 같다. 까닭 글은 견주지 않는다(Guard 는 걸린 규칙을 모두 적고 Arbiter 는 첫 것만 적는다).
- 다름: (Arbiter 규칙 → Guard 규칙) 쌍마다 수와 Guard 의 까닭 보기.
- MS 에 없는 개념은 따로: Guard 가 D 를 걸었거나 SAFE_ACTION 을 낸 것, 그리고 Guard 의 E(어댑터 · 재료 오류).

Runtime.handle 을 감싸 결과의 `guards` 를 모은다(평가 하니스는 handle 의 결과를 밖으로 내지 않는다). 이 파일은 평가 도구다.

둘째 절(`scripted`) -- 사소한 설명 가르기: 모의 provider 는 거의 늘 맞는 제안을 내서 Arbiter 가 ALLOW 만 낸다. 그러면 "같음" 은
ALLOW 끼리의 같음뿐이다. 그래서 같은 런타임 · 진짜 DC 배선에 정해 둔 제안(도구 × 대상 × 인자, 없는 도구 · 없는 대상 · 틀린
인자 포함)을 요청마다 하나씩 넣어 DENY 규칙들에서도 견준다. 허가 둘 × 맥락 예산 둘.
"""
from __future__ import annotations

import collections
import itertools
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for env, name in (("MS_ACTION_PATH", "action"), ("MS_GUARD_PATH", "guard")):
    p = os.environ.get(env, os.path.join(ROOT, "..", name))
    if os.path.isdir(os.path.join(p, name)) and p not in sys.path:
        sys.path.insert(0, p)

import ms.runtime as R  # noqa: E402
from ms import guard_shadow  # noqa: E402
from ms.eval import CONFIGS, evaluate  # noqa: E402

TASKS = ("eval/tasks/datacenter.json", "eval/tasks/datacenter_tasks3.json")


def _head(path):
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


def run() -> dict:
    if not guard_shadow.available():
        raise SystemExit("guard 를 못 읽는다 -- MS_GUARD_PATH(와 MS_ACTION_PATH)에 cogito5170/guard · action 을 두어라")
    rows = []
    orig = R.Runtime.handle

    def handle(self, request):
        out = orig(self, request)
        rows.extend(dict(g, executed=out["result"]["outcome"] == "executed") for g in out["guards"])
        return out
    R.Runtime.handle = handle
    try:
        for t in TASKS:
            evaluate(os.path.join(ROOT, t), {"openai": "sim-openai", "claude": "sim-claude"}, configs=tuple(CONFIGS),
                     reps=2, seed=3, log=lambda *a: None, state_reader="dc")
    finally:
        R.Runtime.handle = orig
    env = {k: os.environ.get(k, os.path.join(ROOT, "..", d)) for k, d in
           (("MS_DC_PATH", "DC"), ("MS_ACTION_PATH", "action"), ("MS_GUARD_PATH", "guard"))}
    return {"versions": {"ms": _head(ROOT), **{k: _head(v) for k, v in env.items()}}, "sim": _tally(rows)}


def _tally(rows) -> dict:
    same, diff, apart, reasons = 0, collections.Counter(), collections.Counter(), {}
    for r in rows:
        a, g = tuple(r["arbiter"]), (r["guard"]["verdict"], r["guard"]["rule"])
        if g[0] == "SAFE_ACTION" or g[1] in ("D", "E"):
            apart[(a, g)] += 1
            reasons.setdefault(f"{a} -> {g}", r["guard"]["reasons"])
        elif a == g:
            same += 1
        else:
            diff[(a, g)] += 1
            reasons.setdefault(f"{a} -> {g}", r["guard"]["reasons"])
    return {"intents": len(rows), "same": same, "different": sum(diff.values()), "apart": sum(apart.values()),
            "by_arbiter": dict(sorted(collections.Counter(f"{r['arbiter'][0]} {r['arbiter'][1]}" for r in rows).items())),
            "diff": {f"{a} -> {g}": n for (a, g), n in diff.most_common()},
            "apart_table": {f"{a} -> {g}": n for (a, g), n in apart.most_common()},
            "reasons": reasons}


ARGS = ({}, {"level": 2}, {"level": 9}, {"note": "팬"})
GRANTS = ((), ("reboot", "open_ticket"))
BUDGETS = (1500, 100000)


def scripted() -> dict:
    from ms import StateManager, ToolRegistry, usage_model as U
    from ms.eval import dc_state_reader
    from ms.llm import ScriptedLLM
    Reader, _ = dc_state_reader()
    ex = os.path.join(ROOT, "ms", "examples")
    with open(os.path.join(ex, "datacenter.json"), encoding="utf-8") as fh:
        spec = json.load(fh)
    rows = []
    for grants, budget in itertools.product(GRANTS, BUDGETS):
        now = [spec["now"]]
        m = StateManager.from_spec(spec, clock=lambda: now[0])
        with open(os.path.join(ex, "datacenter_telemetry.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    m.ingest(json.loads(line))
        reg = ToolRegistry(spec["tools"])
        props = [{"tool": t, "target": e, "args": a, "rationale": "대조"}
                 for t in [x["name"] for x in spec["tools"] if x["name"] != "retrieve"] + ["format_disk"]
                 for e in [x["id"] for x in spec["entities"]] + ["srv99"] for a in ARGS]
        rt = R.Runtime(m, reg, {"p": ScriptedLLM(props)}, grants=grants, base_context={"budget_chars": budget})
        rt.state_reader = Reader(rt.um, m)
        rt.open_session("s", {"token_budget": 100000})
        for _ in props:
            out = rt.handle({"session": "s", "task": "대조", "queries": spec["queries"], "max_rounds": 1})
            rows.extend(out["guards"])
    return _tally(rows)


def main(argv) -> int:
    rep = run()
    rep["scripted"] = scripted()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if len(argv) > 1:
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
