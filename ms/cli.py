"""명령줄.

    python3 -m ms demo                                      # 예시 세계(데이터센터) · 대본 LLM 으로 한 바퀴
    python3 -m ms demo --llm "claude -p"                    # 같은 세계 · 진짜 LLM
    python3 -m ms ingest  SPEC --telemetry T.jsonl          # 적용 · 격리 · 낡음 개수
    python3 -m ms context SPEC --telemetry T.jsonl --task "..."   # LLM 에 갈 최소 맥락만(LLM 안 부름)
    python3 -m ms run     SPEC --telemetry T.jsonl --task "..." --llm "claude -p" [--grant reboot] [--ledger a.jsonl]

SPEC 은 JSON: models · relationships · entities · edges · tools · queries · policy · grants · (선택) now.
`now` 가 있으면 시계를 그 값에 고정한다(예시 · 재현용). 없으면 지금 시각.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time

from .arbiter import WalpArbiter
from .context import ContextPolicy
from .llm import CommandLLM, ScriptedLLM
from .manager import StateManager
from .pipeline import Pipeline
from .tools import ToolRegistry

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_SPEC = os.path.join(HERE, "examples", "datacenter.json")
DEMO_TELEMETRY = os.path.join(HERE, "examples", "datacenter_telemetry.jsonl")
DEMO_TASK = "과열된 서버를 처리하라"
# 대본 LLM -- 진짜 모형이 아니다. 중재자의 세 갈래(근거 없음 · 허가 없음 · 허용)를 보이려고 고른 제안이다
DEMO_SCRIPT = [
    {"tool": "throttle", "target": "srv09", "args": {"level": 1}, "rationale": "요약에만 보이는 서버를 건드려 본다"},
    {"tool": "reboot", "target": "srv07", "rationale": "가장 뜨거운 서버를 재부팅"},
    {"tool": "throttle", "target": "srv07", "args": {"level": 2}, "rationale": "재부팅이 막혔으니 클럭을 낮춘다"},
]


def load(spec_path, telemetry_path=None, now=None):
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    fixed = now if now is not None else spec.get("now")
    clock = (lambda: float(fixed)) if fixed is not None else time.time
    m = StateManager.from_spec(spec, clock=clock)
    reg = ToolRegistry(spec.get("tools", ()))
    results = []
    if telemetry_path:
        with open(telemetry_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    results.append(m.ingest(json.loads(line)))
    return spec, m, reg, results


def _policy(spec, budget):
    kw = dict(spec.get("policy") or {})
    if budget is not None:
        kw["budget_chars"] = budget
    return ContextPolicy(**kw)


def _print_ingest(m):
    print(f"[ingest] {dict(m.counts)}")
    for q in m.quarantine:
        print(f"  격리 {q['status']:<8} {q['entity']}.{q['signal']}: {q['reason']}")


def _print_run(res, ctx_text=None):
    for r in res.rounds:
        c = r["context"]
        print(f"\n[판 {r['round']}] 맥락 {c['chars']}자 (예산 {c['budget']}) · 질의 행 {c['matched']} -> "
              f"KEEP {c['keep']} · SUMMARIZE {c['summarize']} · RETRIEVE {c['retrieve_only']}"
              + (" · 예산 넘음" if c["over_budget"] else ""))
        if "llm_error" in r:
            print(f"  LLM 실패: {r['llm_error']}")
            continue
        p, d = r["proposal"], r["decision"]
        print(f"  제안   {p['tool']} -> {p.get('target')} {json.dumps(p.get('args') or {}, ensure_ascii=False)}"
              + (f"  ({p['error']})" if p.get("error") else ""))
        print(f"  판정   {d['verdict']} [{d['rule']}] " + " / ".join(d["reasons"]))
    for i in res.ingested:
        print(f"  도구 결과 -> 텔레메트리 {i['signal']}: {i['status']} {i['changes'] or i['reason']}")
    print(f"\n결과: {res.outcome}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ms", description="Model-State 층")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "ingest", "context", "run"):
        s = sub.add_parser(name)
        if name != "demo":
            s.add_argument("spec")
            s.add_argument("--telemetry")
        if name in ("context", "run"):
            s.add_argument("--task", required=True)
        if name in ("demo", "run"):
            s.add_argument("--llm", help='LLM 명령(표준입력으로 프롬프트). 예: "claude -p"')
            s.add_argument("--grant", action="append", default=[], help="external · irreversible 도구 허가")
            s.add_argument("--ledger", help="중재자 판정을 JSONL 로 남길 곳")
            s.add_argument("--rounds", type=int, default=4)
        if name in ("demo", "context", "run"):
            s.add_argument("--budget", type=int)
        s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        spec, m, reg, _ = load(DEMO_SPEC, DEMO_TELEMETRY)
        task = DEMO_TASK
    else:
        spec, m, reg, _ = load(a.spec, a.telemetry)
        task = getattr(a, "task", "")

    if a.cmd == "ingest":
        if a.json:
            print(json.dumps({"counts": dict(m.counts), "quarantine": list(m.quarantine)}, ensure_ascii=False))
        else:
            _print_ingest(m)
        return 0

    policy = _policy(spec, a.budget)
    if a.cmd == "context":
        p = Pipeline(m, reg, ScriptedLLM([]), policy)
        ctx = p.context(task, spec.get("queries", ()))
        if a.json:
            print(json.dumps({"stats": ctx.stats(), "context": ctx.payload()}, ensure_ascii=False))
        else:
            print(json.dumps(ctx.payload(), ensure_ascii=False, indent=1))
            print(f"\n{ctx.stats()}", file=sys.stderr)
        return 0

    llm = CommandLLM(shlex.split(a.llm)) if a.llm else ScriptedLLM(DEMO_SCRIPT if a.cmd == "demo" else [])
    grants = set(spec.get("grants") or []) | set(a.grant)
    arb = WalpArbiter(reg, grants, a.ledger, clock=m.clock)
    pipe = Pipeline(m, reg, llm, policy, arb)
    if not a.json:
        _print_ingest(m)
        print(f"[LLM] {a.llm or '대본(진짜 모형 아님)'}")
    res = pipe.run(task, spec.get("queries", ()), max_rounds=a.rounds)
    if a.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False))
    else:
        _print_run(res)
    return 0 if res.outcome in ("executed", "noop") else 1
