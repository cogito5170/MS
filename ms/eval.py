"""평가 하니스 -- 같은 과업 · 같은 성공 기준으로 여섯 칸(A~F)을 돈다. 사전등록: `eval/PREREG_적응정책.md`.

    칸   provider 자리   Context Policy   Prompt Policy
    A    openai          fixed            fixed
    B    claude          fixed            fixed
    C    openai          adaptive         fixed
    D    claude          adaptive         fixed
    E    openai          adaptive         adaptive
    F    claude          adaptive         adaptive

provider 자리에 무엇을 꽂을지는 부르는 쪽이 정한다(`openai:<모형>` · `claude:<모형>` · `claude-cli` · `sim-openai` ...).

한 칸 · 한 반복 = 한 세션. 과업을 파일 순서대로 돌고, **과업마다 세계를 새로 짓는다**(앞 과업의 도구 결과가 뒤 과업의 성공을
대신 만들지 않게). 세션의 사용 상태는 과업을 건너 이어진다 -- 적응 정책은 그것을 본다.

성공은 최종 상태 그래프로 판정한다. 실패하고 과업에 `correction` 이 있으면 **모의 사용자**가 한 번 고친다: 피드백(user_correction=True)
을 넣고, 과업 글에 고침을 붙여 새 세계에서 다시 돈다(success_after_correction). 성공하면 피드백 False 를 넣는다 -- 그래야
correction_rate 상태가 "모름" 에서 벗어난다.

보고서는 provider 사이를 비교하지 않는다. 짝(A↔C · B↔D · C↔E · D↔F)마다 과업 단위 부트스트랩 95% 구간을 낸다.
모의 provider 나 claude-cli 가 섞이면 보고서 머리에 그것을 크게 적는다 -- 그 결과는 가설의 증거가 아니다.
"""
from __future__ import annotations

import json
import random
import statistics
import time

from .cli import load
from .policy import AdaptiveContext, AdaptivePrompt, FixedContext, FixedPrompt
from .providers import make_provider
from .runtime import Runtime
from .usage_model import install
from .manager import StateManager

CONFIGS = {"A": ("openai", "fixed", "fixed"), "B": ("claude", "fixed", "fixed"),
           "C": ("openai", "adaptive", "fixed"), "D": ("claude", "adaptive", "fixed"),
           "E": ("openai", "adaptive", "adaptive"), "F": ("claude", "adaptive", "adaptive")}
PAIRS = [("A", "C", "적응 맥락(openai)"), ("B", "D", "적응 맥락(claude)"),
         ("C", "E", "적응 프롬프트 더함(openai)"), ("D", "F", "적응 프롬프트 더함(claude)")]
METRICS = ("input_tokens", "output_tokens", "total_tokens", "total_ms", "retries", "cost_usd")
DELTA = 0.05          # 품질 비열등 한계(사전등록)


def parse_slot(spec: str):
    """'openai:gpt-x' -> ('openai', 'gpt-x'), 'claude-cli' -> ('claude-cli', None)."""
    name, _, model = spec.partition(":")
    return name, (model or None)


def _world(tasks_file: dict, task: dict):
    spec, m, reg, _ = load(tasks_file["world"]["spec"], tasks_file["world"]["telemetry"])
    for t in task.get("extra_telemetry") or []:
        m.ingest(t)
    return spec, m, reg


def run_config(letter: str, slots: dict, tasks_file: dict, reps: int, provider_kw=None, log=print) -> list:
    slot, cmode, pmode = CONFIGS[letter]
    pname, model = parse_slot(slots[slot])
    provider = make_provider(pname, model, **(provider_kw or {}).get(pname, {}))
    rows = []
    for rep in range(reps):
        usage = StateManager(clock=time.time)
        install(usage)
        sess = f"{letter}-r{rep}"
        first = True
        for task in tasks_file["tasks"]:
            for attempt in (0, 1):
                spec, world, reg = _world(tasks_file, task)
                rt = Runtime(world, reg, {pname: provider}, grants=tasks_file.get("grants", ()),
                             context_selector=AdaptiveContext() if cmode == "adaptive" else FixedContext(),
                             prompt_selector=AdaptivePrompt() if pmode == "adaptive" else FixedPrompt(),
                             base_context=tasks_file.get("base_context"), max_rounds=tasks_file.get("max_rounds", 4),
                             usage_manager=usage, prices=tasks_file.get("prices"))
                if first:
                    rt.open_session(sess, tasks_file["budgets"])
                    first = False
                text = task["task"] if attempt == 0 else f"{task['task']}\n사용자 고침: {task['correction']}"
                req = {"session": sess, "task": text, "queries": task.get("queries") or spec["queries"],
                       **{k: task[k] for k in ("success", "forbidden", "expect_noop") if k in task}}
                out = rt.handle(req)
                rec = out["record"]
                ok = rec["outcome"]["task_success"]
                if attempt == 0:
                    row = _row(letter, rep, task, rec, out["result"])
                    rows.append(row)
                    log(f"  {letter} r{rep} {task['id']:<18} {'성공' if ok else '실패'} "
                        f"in={rec['tokens']['input_tokens']} ms={rec['latency']['total_ms']:.0f} "
                        f"walp={rec['policy']['walp_decision']['all']}")
                    if ok or not task.get("correction"):
                        rt.feedback(rec["run"]["run_id"], False)
                        break
                    rt.feedback(rec["run"]["run_id"], True)
                    row["user_correction"] = True
                else:
                    row["success_after_correction"] = bool(ok)
                    log(f"  {letter} r{rep} {task['id']:<18} 고친 뒤 {'성공' if ok else '실패'}")
    return rows


def _row(letter, rep, task, rec, result) -> dict:
    decisions = rec["policy"]["walp_decision"]["all"]
    st = rec["policy"]["state"]
    return {"config": letter, "rep": rep, "task": task["id"], "success": rec["outcome"]["task_success"],
            "user_correction": False, "success_after_correction": None,
            "forbidden_executed": any(e["tool"] in (task.get("forbidden") or []) for e in result["executed"]),
            "input_tokens": rec["tokens"]["input_tokens"], "output_tokens": rec["tokens"]["output_tokens"],
            "total_tokens": rec["tokens"]["total_tokens"], "cached_input_tokens": rec["tokens"]["cached_input_tokens"],
            "context_tokens": rec["tokens"]["context_tokens"], "total_ms": rec["latency"]["total_ms"],
            "inference_ms": rec["latency"]["inference_ms"], "ttft_ms": rec["latency"]["ttft_ms"],
            "retries": rec["interaction"]["retries"], "tool_calls": rec["interaction"]["tool_calls"],
            "retrievals": rec["interaction"]["context_retrievals"], "llm_calls": rec["interaction"]["llm_calls"],
            "denies": rec["interaction"]["walp_denies"], "cost_usd": rec["cost"]["usd"],
            "recovered": bool(rec["outcome"]["task_success"]) if any(v == "DENY" for v, _ in decisions) else None,
            "state_known": any(v is not None for k, v in st.items() if k != "model_version"),
            "context_plan": rec["policy"]["context_policy"]["reasons"], "prompt_plan": rec["policy"]["prompt_policy"]["reasons"],
            "unsupported": rec["unsupported"], "outcome": result["outcome"], "simulated": rec["run"]["simulated"]}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def summarize(rows: list) -> dict:
    n = len(rows)
    calls = sum(r["llm_calls"] for r in rows)
    with_deny = [r for r in rows if r["recovered"] is not None]
    return {"runs": n, "task_success": _mean([float(bool(r["success"])) for r in rows]),
            "user_corrections": sum(1 for r in rows if r["user_correction"]),
            "success_after_correction": _mean([float(r["success_after_correction"]) for r in rows
                                               if r["success_after_correction"] is not None]),
            "forbidden_executed": sum(1 for r in rows if r["forbidden_executed"]),
            "retries": _mean([r["retries"] for r in rows]), "input_tokens": _mean([r["input_tokens"] for r in rows]),
            "output_tokens": _mean([r["output_tokens"] for r in rows]), "total_tokens": _mean([r["total_tokens"] for r in rows]),
            "cached_input_tokens": _mean([r["cached_input_tokens"] for r in rows]),
            "total_ms_median": statistics.median([r["total_ms"] for r in rows]) if rows else None,
            "cost_usd": None if any(r["cost_usd"] is None for r in rows) else sum(r["cost_usd"] for r in rows),
            "tool_calls": sum(r["tool_calls"] for r in rows), "retrievals": sum(r["retrievals"] for r in rows),
            "walp_deny_rate": (sum(r["denies"] for r in rows) / calls) if calls else None,
            "walp_recovery_rate": _mean([float(r["recovered"]) for r in with_deny]),
            "runs_with_deny": len(with_deny)}


def _per_task(rows, metric):
    by = {}
    for r in rows:
        v = r[metric]
        if metric == "success":
            v = float(bool(v))
        if v is not None:
            by.setdefault(r["task"], []).append(v)
    return {k: statistics.fmean(v) for k, v in by.items()}


def bootstrap(diffs: list, n: int = 2000, seed: int = 0):
    if not diffs:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(n))
    return [means[int(0.025 * n)], means[int(0.975 * n) - 1]]


def compare(rows_by: dict) -> list:
    out = []
    for fixed, adapt, what in PAIRS:
        if fixed not in rows_by or adapt not in rows_by:
            continue
        a, b = rows_by[fixed], rows_by[adapt]
        item = {"pair": f"{fixed}->{adapt}", "what": what, "metrics": {}}
        sa, sb = _per_task(a, "success"), _per_task(b, "success")
        common = sorted(set(sa) & set(sb))
        d = [sb[t] - sa[t] for t in common]
        ci = bootstrap(d)
        q = {"diff": statistics.fmean(d) if d else None, "ci95": ci}
        if ci is None:
            q["verdict"] = "판정 불가"
        elif ci[0] >= -DELTA:
            q["verdict"] = "비열등"
        elif q["diff"] < -DELTA:
            q["verdict"] = "열등(실격)"
        else:
            q["verdict"] = "판정 불가(구간이 넓다)"
        item["quality"] = q
        item["safety_ok"] = not any(r["forbidden_executed"] for r in b)
        for m in METRICS:
            pa, pb = _per_task(a, m), _per_task(b, m)
            common = sorted(set(pa) & set(pb))
            d = [pb[t] - pa[t] for t in common]
            ci = bootstrap(d)
            if ci is None:
                verdict = "모른다"
            elif ci[0] == 0 == ci[1]:
                verdict = "같다"
            elif ci[0] <= 0 <= ci[1]:
                verdict = "모른다"
            else:
                verdict = "줄었다" if ci[1] < 0 else "늘었다"
            item["metrics"][m] = {"fixed": _mean(pa.values()), "adaptive": _mean(pb.values()),
                                  "diff": statistics.fmean(d) if d else None, "ci95": ci, "verdict": verdict,
                                  "tasks": len(common)}
        # 사소한 설명: 성공한 과업만으로도 토큰
        okt = {r["task"] for r in a if r["success"]} & {r["task"] for r in b if r["success"]}
        pa, pb = _per_task([r for r in a if r["task"] in okt], "input_tokens"), \
            _per_task([r for r in b if r["task"] in okt], "input_tokens")
        d = [pb[t] - pa[t] for t in sorted(set(pa) & set(pb))]
        item["input_tokens_success_only"] = {"diff": statistics.fmean(d) if d else None, "ci95": bootstrap(d)}
        item["adaptive_state_known_runs"] = sum(1 for r in b if r["state_known"])
        item["adaptive_runs"] = len(b)
        out.append(item)
    return out


def report_md(rep: dict) -> str:
    L = []
    if rep["not_evidence"]:
        L += ["> **이 결과는 가설의 증거가 아니다.** " + rep["not_evidence"], ""]
    L += [f"# 평가 -- {rep['tasks_file']} · 반복 {rep['reps']} · {rep['started']}", "",
          "## provider 능력(같다고 가정하지 않는다)", "", "| 자리 | 무엇 | 능력 |", "|---|---|---|"]
    for slot, cap in rep["capabilities"].items():
        L.append(f"| {slot} | {rep['slots'][slot]} | " + " · ".join(f"{k}: {v}" for k, v in cap.items()
                                                              if k not in ("provider", "model")) + " |")
    L += ["", "## 칸별", "", "| 칸 | 성공 | 고침 | 금지 실행 | 재시도 | 입력 | 출력 | 총 토큰 | 지연 중앙(ms) | 비용 | 도구 | 꺼냄 | DENY 율 | 회복 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def f(v, k=1):
        return "-" if v is None else (f"{v:.{k}f}" if isinstance(v, float) else str(v))
    for c, s in rep["summary"].items():
        L.append(f"| {c} | {f(s['task_success'], 2)} | {s['user_corrections']} | {s['forbidden_executed']} | "
                 f"{f(s['retries'], 2)} | {f(s['input_tokens'], 0)} | {f(s['output_tokens'], 0)} | {f(s['total_tokens'], 0)} | "
                 f"{f(s['total_ms_median'], 0)} | {f(s['cost_usd'], 4)} | {s['tool_calls']} | {s['retrievals']} | "
                 f"{f(s['walp_deny_rate'], 2)} | {f(s['walp_recovery_rate'], 2)} |")
    L += ["", "## 짝 비교(사전등록 판정)", ""]
    for c in rep["comparisons"]:
        q = c["quality"]
        L.append(f"### {c['pair']} -- {c['what']}")
        L.append(f"- 품질: 성공률 차 {f(q['diff'], 3)} 구간 {q['ci95']} -> **{q['verdict']}** · 안전 {'통과' if c['safety_ok'] else '**실격**'}")
        L.append(f"- 적응 칸에서 상태가 정해진 실행 {c['adaptive_state_known_runs']}/{c['adaptive_runs']}")
        for m, v in c["metrics"].items():
            k = 5 if m == "cost_usd" else 1
            L.append(f"- {m}: 고정 {f(v['fixed'], k)} -> 적응 {f(v['adaptive'], k)} · 차 {f(v['diff'], k)} 구간 "
                     f"{[round(x, k) for x in v['ci95']] if v['ci95'] else None} -> {v['verdict']} (과업 {v['tasks']})")
        L.append("")
    return "\n".join(L)


def evaluate(tasks_path: str, slots: dict, configs=("A", "B", "C", "D", "E", "F"), reps: int = 3,
             provider_kw=None, log=print) -> dict:
    with open(tasks_path, encoding="utf-8") as fh:
        tf = json.load(fh)
    rows_by, caps = {}, {}
    for c in configs:
        slot = CONFIGS[c][0]
        if slot not in slots:
            log(f"[{c}] {slot} 자리가 비었다 -- 건너뛴다")
            continue
        log(f"[{c}] {slots[slot]} · context={CONFIGS[c][1]} · prompt={CONFIGS[c][2]}")
        rows_by[c] = run_config(c, slots, tf, reps, provider_kw, log)
        pname, model = parse_slot(slots[slot])
        caps.setdefault(slot, make_provider(pname, model, **(provider_kw or {}).get(pname, {})).capabilities())
    sims = sorted({slots[CONFIGS[c][0]] for c in rows_by if any(r["simulated"] for r in rows_by[c])})
    cli = sorted({slots[CONFIGS[c][0]] for c in rows_by if slots[CONFIGS[c][0]].startswith("claude-cli")})
    note = []
    if sims:
        note.append(f"모의 provider({', '.join(sims)}) -- 토큰 · 지연 · 응답을 지어냈다. 배선 확인일 뿐이다")
    if cli:
        note.append("claude-cli 는 Claude Code 하네스가 붙어 API 의 Claude 와 같지 않다(사전등록: B · D · F 가 아니라 따로)")
    rep = {"tasks_file": tasks_path, "reps": reps, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "slots": slots,
           "capabilities": caps, "not_evidence": " / ".join(note),
           "summary": {c: summarize(r) for c, r in rows_by.items()}, "comparisons": compare(rows_by),
           "rows": [r for c in rows_by for r in rows_by[c]], "prereg": "eval/PREREG_적응정책.md"}
    return rep
