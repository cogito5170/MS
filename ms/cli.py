"""명령줄.

    python3 -m ms demo                                      # 예시 세계(데이터센터) · 대본 LLM 으로 한 바퀴
    python3 -m ms demo --llm "claude -p"                    # 같은 세계 · 진짜 LLM
    python3 -m ms ingest  SPEC --telemetry T.jsonl          # 적용 · 격리 · 낡음 개수
    python3 -m ms context SPEC --telemetry T.jsonl --task "..."   # LLM 에 갈 최소 맥락만(LLM 안 부름)
    python3 -m ms run     SPEC --telemetry T.jsonl --task "..." --llm "claude -p" [--grant reboot] [--ledger a.jsonl]

    # Policy Runtime (MS API): 상태 -> 정책 -> provider -> 제안 -> Arbiter, 실행은 정규 텔레메트리로 상태에 되돌아간다
    python3 -m ms ask  SPEC --telemetry T.jsonl --task "..." --provider claude-cli [--model M]
                       [--context adaptive] [--prompt adaptive] [--stream] [--json]
    python3 -m ms eval --tasks eval/tasks/datacenter.json --openai openai:<모형> --claude claude:claude-opus-5-5
                       [--configs A,B,C,D,E,F] [--reps 3] [--out eval/results/이름]
                       [--state-reader dc] [--prereg eval/PREREG_….md]      (F2: --configs B,D,G)

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

from .arbiter import Arbiter
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


def clock_for(spec: dict, now=None):
    """세계의 시계: 명세에 `now` 가 있으면 그 시각에 멈춘 시계, 없으면 벽시계. 시계를 고르는 곳은 여기 하나다(PC-12)."""
    fixed = now if now is not None else spec.get("now")
    return (lambda: float(fixed)) if fixed is not None else time.time


def load(spec_path, telemetry_path=None, now=None, clock=None):
    """clock 을 주면 그 시계를 쓴다(평가처럼 세계를 여러 번 지어도 시계는 하나여야 할 때)."""
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    m = StateManager.from_spec(spec, clock=clock or clock_for(spec, now))
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
    s = sub.add_parser("ask")
    s.add_argument("spec")
    s.add_argument("--telemetry")
    s.add_argument("--task", required=True)
    s.add_argument("--provider", required=True, help="openai · claude · gemini · claude-cli · sim-openai ...")
    s.add_argument("--model")
    s.add_argument("--context", choices=("fixed", "adaptive"), default="fixed")
    s.add_argument("--prompt", choices=("fixed", "adaptive"), default="fixed")
    s.add_argument("--stream", action="store_true")
    s.add_argument("--grant", action="append", default=[])
    s.add_argument("--ledger")
    s.add_argument("--l0-ledger", help="L0 Telemetry 사건 원장(JSONL). cogito5170/Telemetry 가 깔려 있어야 한다")
    s.add_argument("--budget", type=int)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("eval")
    s.add_argument("--tasks", required=True)
    s.add_argument("--openai", help="openai 자리: openai:<모형> · sim-openai")
    s.add_argument("--claude", help="claude 자리: claude:<모형> · claude-cli[:모형] · sim-claude")
    s.add_argument("--configs", default="A,B,C,D,E,F")
    s.add_argument("--reps", type=int, default=3)
    s.add_argument("--out", help="결과를 <out>.json · <out>.md 로")
    s.add_argument("--order", choices=("interleaved", "blocked"), default="interleaved",
                   help="interleaved: 과업마다 칸 순서를 섞는다(provider 캐시 치우침을 줄인다)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--layout", choices=("stable_prefix", "legacy"), help="프롬프트 배치(기본 stable_prefix)")
    s.add_argument("--state-reader", choices=("dc",), help="상태를 DC 결정 문맥으로 읽는다(MS_DC_PATH, 기본 ../DC)")
    s.add_argument("--cost-limit", type=float, help="provider 보고 비용 합(USD)이 이것을 넘으면 멈춘다")
    s.add_argument("--prereg", default="eval/PREREG_적응정책.md", help="이 실행이 따르는 사전등록 문서(보고에 적힌다)")
    s.add_argument("--fresh", action="store_true",
                   help="실행마다 다른 표지를 시스템 글 뒤에 붙여, 되풀이한 과업이 프롬프트 전체를 캐시에서 읽지 않게 한다")
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

    if a.cmd == "eval":
        return _eval(a)
    if a.cmd == "ask":
        return _ask(a)
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
    arb = Arbiter(reg, grants, a.ledger, clock=m.clock)
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


def _ask(a):
    from .policy import AdaptiveContext3 as AdaptiveContext, AdaptivePrompt2 as AdaptivePrompt, FixedContext, FixedPrompt  # BD-88 · BD-91
    from .providers import make_provider
    from .runtime import Runtime
    spec, m, reg, _ = load(a.spec, a.telemetry)
    prov = make_provider(a.provider, a.model)
    base = dict(spec.get("policy") or {})
    if a.budget is not None:
        base["budget_chars"] = a.budget
    rt = Runtime(m, reg, {a.provider: prov}, grants=set(spec.get("grants") or []) | set(a.grant),
                 context_selector=AdaptiveContext() if a.context == "adaptive" else FixedContext(),
                 prompt_selector=AdaptivePrompt() if a.prompt == "adaptive" else FixedPrompt(),
                 base_context=base, ledger_path=a.ledger, l0_ledger=a.l0_ledger)
    rt.open_session("cli", spec.get("budgets") or {})
    out = rt.handle({"session": "cli", "task": a.task, "queries": spec.get("queries", ()), "stream": a.stream})
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        _print_run(_Res(out["result"]))
        r = out["record"]
        print(f"\n[telemetry] provider={r['run']['provider']} model={r['run']['model']} tokens={r['tokens']} "
              f"latency={r['latency']} cost={r['cost']} unsupported={r['unsupported']}")
    return 0 if out["result"]["outcome"] in ("executed", "noop") else 1


class _Res:
    def __init__(self, d):
        self.rounds, self.ingested, self.outcome = d["rounds"], d["ingested"], d["outcome"]


def _eval(a):
    from .eval import evaluate, report_md
    slots = {k: v for k, v in (("openai", a.openai), ("claude", a.claude)) if v}
    rep = evaluate(a.tasks, slots, tuple(c.strip() for c in a.configs.split(",") if c.strip()), a.reps,
                   order=a.order, seed=a.seed, layout=a.layout, fresh=a.fresh, state_reader=a.state_reader,
                   prereg=a.prereg, cost_limit=a.cost_limit)
    md = report_md(rep)
    if a.out:
        with open(a.out + ".json", "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        with open(a.out + ".md", "w", encoding="utf-8") as f:
            f.write(md + "\n")
        print(f"-> {a.out}.json · {a.out}.md")
    print(md)
    return 0
