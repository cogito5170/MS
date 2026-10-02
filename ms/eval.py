"""평가 하니스 -- 같은 과업 · 같은 성공 기준으로 여섯 칸(A~F)을 돈다. 사전등록: `eval/PREREG_적응정책.md`.

    칸   provider 자리   Context Policy   Prompt Policy
    A    openai          fixed            fixed
    B    claude          fixed            fixed
    C    openai          adaptive         fixed
    D    claude          adaptive         fixed
    E    openai          adaptive         adaptive
    F    claude          adaptive         adaptive
    G    claude          adaptive2        fixed        (사전등록 PREREG_F2_꺼냄과지연.md: 압력 HIGH 에서 DROP · DEFER 안 함)

provider 자리에 무엇을 꽂을지는 부르는 쪽이 정한다(`openai:<모형>` · `claude:<모형>` · `claude-cli` · `sim-openai` ...).

한 칸 · 한 반복 = 한 세션. 과업을 파일 순서대로 돌고, **과업마다 세계를 새로 짓는다**(앞 과업의 도구 결과가 뒤 과업의 성공을
대신 만들지 않게). 세션의 사용 상태는 과업을 건너 이어진다 -- 적응 정책은 그것을 본다.

성공은 **이 하니스가** 최종 상태 그래프로 판정한다(`judge`). Runtime 은 판정하지 않는다(PC-13) -- 판정을 `Runtime.evaluation()` 으로
돌려주면 세션의 사용 상태가 그것을 관측으로 받는다. 실패하고 과업에 `correction` 이 있으면 **모의 사용자**가 한 번 고친다: 피드백(user_correction=True)
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

from . import predicate
from .cli import clock_for, load
from .policy import AdaptiveContext3, AdaptiveContext4, AdaptiveContext4c, AdaptivePrompt2, FixedContext, FixedPrompt
from . import usage_model as U
from .providers import make_provider
from .runtime import Runtime
from .usage_model import install
from .manager import StateManager

CONFIGS = {"A": ("openai", "fixed", "fixed"), "B": ("claude", "fixed", "fixed"),
           "C": ("openai", "adaptive", "fixed"), "D": ("claude", "adaptive", "fixed"),
           "E": ("openai", "adaptive", "adaptive"), "F": ("claude", "adaptive", "adaptive"),
           "G": ("claude", "adaptive2", "fixed"), "H": ("claude", "adaptive2c", "fixed")}
# BD-88: 평가의 적응 칸은 품질 상태를 모르면 KEEP 하는 판본으로 돈다(ctx-adaptive-3 · -4 · -4c). 옛 판본은 재현에만 남는다
CONTEXT = {"fixed": FixedContext, "adaptive": AdaptiveContext3, "adaptive2": AdaptiveContext4, "adaptive2c": AdaptiveContext4c}
PAIRS = [("A", "C", "적응 맥락(openai)"), ("B", "D", "적응 맥락(claude)"),
         ("C", "E", "적응 프롬프트 더함(openai)"), ("D", "F", "적응 프롬프트 더함(claude)"),
         ("D", "G", "덜 자르는 적응 맥락(claude)"), ("B", "G", "덜 자르는 적응 맥락 대 고정(claude)"),
         ("G", "H", "덮음 선언(claude)"), ("B", "H", "덮음 선언 대 고정(claude)")]
METRICS = ("input_tokens", "uncached_input_tokens", "output_tokens", "total_tokens", "total_ms", "retries", "cost_usd",
           "rationale_chars", "retrievals", "llm_calls")
LOTO = ("retrievals", "total_ms")     # 사소한 설명 S1: 과업 하나씩 빼고 다시 낸다
DELTA = 0.05          # 품질 비열등 한계(사전등록)


def parse_slot(spec: str):
    """'openai:gpt-x' -> ('openai', 'gpt-x'), 'claude-cli' -> ('claude-cli', None)."""
    name, _, model = spec.partition(":")
    return name, (model or None)


def judge(world, task: dict, executed: list):
    """과업의 성공 기준을 **최종 상태 그래프**로 본다. 기준이 없으면 None. LLM 의 말은 보지 않는다."""
    if not any(k in task for k in ("success", "forbidden", "expect_noop")):
        return None
    tools = [e["tool"] for e in executed]
    if any(t in (task.get("forbidden") or []) for t in tools):
        return False
    if task.get("expect_noop") and tools:
        return False
    for ent, prop, *rest in task.get("success") or []:
        node = world.graph.nodes.get(ent)
        if node is None or not predicate.holds([prop, *rest], node.values()):
            return False
    return True


def _world(tasks_file: dict, task: dict, clock):
    spec, m, reg, _ = load(tasks_file["world"]["spec"], tasks_file["world"]["telemetry"], clock=clock)
    for t in task.get("extra_telemetry") or []:
        m.ingest(t)
    return spec, m, reg


class Lane:
    """한 칸 · 한 반복 = 한 세션. 세션의 사용 상태가 과업을 건너 이어진다."""

    def __init__(self, letter, rep, slots, tasks_file, provider_kw=None, layout=None, fresh=None, state_reader=None):
        slot, self.cmode, self.pmode = CONFIGS[letter]
        self.pname, model = parse_slot(slots[slot])
        self.provider = make_provider(self.pname, model, **(provider_kw or {}).get(self.pname, {}))
        self.letter, self.rep, self.tf = letter, rep, tasks_file
        with open(tasks_file["world"]["spec"], encoding="utf-8") as f:
            self.clock = clock_for(json.load(f))        # 시계 하나: 이 세션의 사용 상태와 과업마다 새로 짓는 세계가 같이 쓴다(PC-12)
        self.usage = StateManager(clock=self.clock)
        install(self.usage)
        self.sess, self.opened = f"{letter}-r{rep}", False
        self.layout, self.fresh = layout, fresh          # fresh: 이 평가 실행의 표지(None 이면 안 붙임)
        self.make_reader = state_reader        # (사용 상태, 세계) -> 리더. 과업마다 세계가 새로 서서 리더도 그때 짓는다
        self.reader = None

    def run_task(self, task, log=print) -> dict:
        tf, row = self.tf, None
        for attempt in (0, 1):
            spec, world, reg = _world(tf, task, self.clock)
            self.reader = self.make_reader(self.usage, world) if self.make_reader else None
            rt = Runtime(world, reg, {self.pname: self.provider}, grants=tf.get("grants", ()),
                         context_selector=CONTEXT[self.cmode](),
                         state_reader=self.reader,
                         prompt_selector=AdaptivePrompt2() if self.pmode == "adaptive" else FixedPrompt(),   # BD-91
                         base_context=tf.get("base_context"), max_rounds=tf.get("max_rounds", 4),
                         usage_manager=self.usage, prices=tf.get("prices"), prompt_layout=self.layout)
            if not self.opened:
                rt.open_session(self.sess, tf["budgets"])
                self.opened = True
            text = task["task"] if attempt == 0 else f"{task['task']}\n사용자 고침: {task['correction']}"
            pre = f"run {self.fresh}/{self.letter}-r{self.rep}/{task['id']}/{attempt}" if self.fresh else ""
            req = {"session": self.sess, "task": text, "queries": task.get("queries") or spec["queries"], "preamble": pre}
            out = rt.handle(req)
            rec = out["record"]
            ok = judge(world, task, out["result"]["executed"])
            if ok is not None:
                rt.evaluation(rec["run"]["run_id"], ok)
            if attempt == 0:
                row = _row(self.letter, self.rep, task, rec, out["decision"], out["result"], ok)
                row["dc_diff"] = getattr(self.reader, "diff", None)       # S5: DC 상태가 snapshot 과 다른 상태 이름
                log(f"  {self.letter} r{self.rep} {task['id']:<18} {'성공' if ok else '실패'} "
                    f"in={rec['tokens']['input_tokens']} 캐시={rec['tokens']['cached_input_tokens']} "
                    f"ms={rec['latency']['total_ms']:.0f} $={rec['cost']['usd']} arbiter={out['decision']['arbiter_decision']['all']}")
                if ok or not task.get("correction"):
                    rt.feedback(rec["run"]["run_id"], False)
                    break
                rt.feedback(rec["run"]["run_id"], True)
                row["user_correction"] = True
            else:
                row["success_after_correction"] = bool(ok)
                log(f"  {self.letter} r{self.rep} {task['id']:<18} 고친 뒤 {'성공' if ok else '실패'}")
        return row


def run_all(configs, slots, tasks_file, reps, provider_kw=None, order="interleaved", seed=0, log=print,
            layout=None, fresh=None, state_reader=None, budget: "dict | None" = None) -> dict:
    """order=interleaved: 반복마다 · 과업마다 칸 순서를 씨앗 고정 난수로 섞는다. 앞 칸이 쓴 provider 캐시를 늘 같은 칸이
    읽는 치우침을 줄인다(재측정에서 실제로 났다). 그래도 **같은 지시문을 쓰는 칸끼리는 캐시를 나눠 쓴다** -- 그래서
    캐시 안 된 입력(`uncached_input_tokens`)도 따로 비교한다. order=blocked: 칸마다 통째로(예전 방식)."""
    rng = random.Random(seed)
    rows_by = {c: [] for c in configs}

    def spend(row) -> bool:
        """비용 한도(budget['limit'], provider 보고 USD 합). 넘으면 멈춘다 -- 남은 실행을 돌리지 않는다. 측정 자체는 바꾸지 않는다."""
        if budget is None:
            return False
        budget["spent"] = budget.get("spent", 0.0) + (row.get("cost_usd") or 0.0)
        budget["runs"] = budget.get("runs", 0) + 1
        if budget.get("limit") is not None and budget["spent"] > budget["limit"]:
            budget["stopped"] = {"after_runs": budget["runs"], "spent": budget["spent"]}
            log(f"!! 비용 한도 ${budget['limit']} 를 넘었다(${budget['spent']:.4f}) -- 멈춘다")
            return True
        return False
    if order == "blocked":
        for c in configs:
            for rep in range(reps):
                lane = Lane(c, rep, slots, tasks_file, provider_kw, layout, fresh, state_reader)
                for task in tasks_file["tasks"]:
                    rows_by[c].append(lane.run_task(task, log))
                    if spend(rows_by[c][-1]):
                        return rows_by
        return rows_by
    for rep in range(reps):
        lanes = {c: Lane(c, rep, slots, tasks_file, provider_kw, layout, fresh, state_reader) for c in configs}
        for i, task in enumerate(tasks_file["tasks"]):
            seq = list(configs)
            rng.shuffle(seq)
            for c in seq:
                row = lanes[c].run_task(task, log)
                row["order"] = [rep, i, seq.index(c)]
                rows_by[c].append(row)
                if spend(row):
                    return rows_by
    return rows_by


def _row(letter, rep, task, rec, dec, result, ok) -> dict:
    decisions = dec["arbiter_decision"]["all"]
    st = dec["state"]
    rats = [len(r["proposal"].get("rationale") or "") for r in result["rounds"] if "proposal" in r]
    prefixes = sorted({c["cd"]["prefix_hash"] for c in result["calls"] if c.get("cd")})
    return {"config": letter, "rep": rep, "task": task["id"], "success": ok,
            "user_correction": False, "success_after_correction": None,
            "forbidden_executed": any(e["tool"] in (task.get("forbidden") or []) for e in result["executed"]),
            "executed": [[e["tool"], e.get("target")] for e in result["executed"]],      # 무엇을 했나(판정을 나중에 다시 볼 수 있게)
            "input_tokens": rec["tokens"]["input_tokens"], "output_tokens": rec["tokens"]["output_tokens"],
            "total_tokens": rec["tokens"]["total_tokens"], "cached_input_tokens": rec["tokens"]["cached_input_tokens"],
            "uncached_input_tokens": None if rec["tokens"]["input_tokens"] is None else
            rec["tokens"]["input_tokens"] - (rec["tokens"]["cached_input_tokens"] or 0),
            "model_version": st.get("model_version"),
            "context_tokens": rec["tokens"]["context_tokens"], "total_ms": rec["latency"]["total_ms"],
            "inference_ms": rec["latency"]["inference_ms"], "ttft_ms": rec["latency"]["ttft_ms"],
            "retries": rec["interaction"]["retries"], "tool_calls": rec["interaction"]["tool_calls"],
            "retrievals": rec["interaction"]["context_retrievals"], "llm_calls": rec["interaction"]["llm_calls"],
            "denies": rec["interaction"]["arbiter_denies"], "cost_usd": rec["cost"]["usd"],
            "recovered": bool(ok) if any(v == "DENY" for v, _ in decisions) else None,
            "state_known": any(v is not None for k, v in st.items() if k != "model_version"),
            "context_plan": dec["context_policy"]["reasons"], "prompt_plan": dec["prompt_policy"]["reasons"],
            "unsupported": rec["unsupported"], "outcome": result["outcome"], "simulated": rec["run"]["simulated"], "model": rec["run"]["model"],
            "rationale_chars": _mean(rats), "prefix_hashes": prefixes, "prompt_template": dec["prompt_policy"].get("template"),
            "instruction_mode": dec["prompt_policy"]["plan"]["instruction_mode"],
            "context_version": dec["context_policy"]["version"],
            "warmup": bool(task.get("warmup")), "stratum": task.get("stratum"), "pair": task.get("pair"),
            "quality_known": st.get("answer_reliability") is not None and st.get("correction_rate") is not None,   # BD-88 워밍업
            "retrieved_handles": [r["proposal"].get("target") for r in result["rounds"]           # S7: 무엇을 꺼냈나
                                  if r.get("proposal", {}).get("tool") == "retrieve" and r.get("decision", {}).get("verdict") == "ALLOW"],
            "pressure_high": any(x.startswith("압력 HIGH") for x in dec["context_policy"]["reasons"]),   # S4 동작점
            "state_source": dec.get("state_source", {}).get("kind")}


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
            "uncached_input_tokens": _mean([r["uncached_input_tokens"] for r in rows]),
            "total_ms_median": statistics.median([r["total_ms"] for r in rows]) if rows else None,
            "cost_usd": None if any(r["cost_usd"] is None for r in rows) else sum(r["cost_usd"] for r in rows),
            "tool_calls": sum(r["tool_calls"] for r in rows), "retrievals": sum(r["retrievals"] for r in rows),
            "arbiter_deny_rate": (sum(r["denies"] for r in rows) / calls) if calls else None,
            "arbiter_recovery_rate": _mean([float(r["recovered"]) for r in with_deny]),
            "runs_with_deny": len(with_deny),
            "distinct_prefixes": len({h for r in rows for h in r.get("prefix_hashes") or []}),
            "pressure_high_runs": sum(1 for r in rows if r.get("pressure_high")),
            "dc_differs_from_snapshot": sum(1 for r in rows if r.get("dc_diff")),
            "dc_diff_states": sorted({k for r in rows for k in r.get("dc_diff") or []}),
            "models": sorted({str(r.get("model")) for r in rows})}


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
        item["leave_one_task_out"] = {m: _loto(a, b, m) for m in LOTO}
        item["adaptive_pressure_high_runs"] = sum(1 for r in b if r.get("pressure_high"))
        item["adaptive_state_known_runs"] = sum(1 for r in b if r["state_known"])
        item["adaptive_runs"] = len(b)
        out.append(item)
    return out


def _by_task(rows, metric, stratum):
    return _per_task([r for r in rows if r.get("stratum") == stratum], metric)


def _strat_ci(a_diffs: list, b_diffs: list, n: int = 2000, seed: int = 0):
    """층화 부트스트랩: 층마다 과업을 따로 다시 뽑아 (a 층 평균 − b 층 평균). eval/power_F2b.py 와 같은 꼴."""
    if not a_diffs or not b_diffs:
        return None
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choice(a_diffs) for _ in a_diffs) - statistics.fmean(rng.choice(b_diffs) for _ in b_diffs)
                for _ in range(n))
    return [ms[int(0.025 * n)], ms[int(0.975 * n) - 1]]


def _pair_diff(rows_by, x, y, metric, stratum):
    if x not in rows_by or y not in rows_by:
        return None
    a, b = _by_task(rows_by[x], metric, stratum), _by_task(rows_by[y], metric, stratum)
    common = sorted(set(a) & set(b))
    d = [b[t] - a[t] for t in common]
    return {"diff": statistics.fmean(d) if d else None, "ci95": bootstrap(d), "tasks": len(common),
            "per_task": {t: b[t] - a[t] for t in common}, "loto_sign_changes": _loto_list(d, common)}


def _loto_list(d, names):
    if not d:
        return []
    full = statistics.fmean(d)
    sign = lambda v: (v > 0) - (v < 0)
    out = []
    for i, t in enumerate(names):
        rest = d[:i] + d[i + 1:]
        if rest and sign(statistics.fmean(rest)) != sign(full):
            out.append(t)
    return out


def stratified(rows_by: dict) -> "dict | None":
    """F2b 의 판정(사전등록 eval/PREREG_F2b_없음확인.md §2 · 고침 1). 과업에 stratum 이 없으면 None."""
    if not any(r.get("stratum") for rs in rows_by.values() for r in rs):
        return None
    out = {}

    def judge(ci, want):          # want: "above" -> 하한 > 0, "below" -> 상한 < 0
        if ci is None:
            return "판정 불가"
        if want == "above":
            return "확인" if ci[0] > 0 else "모른다"
        return "확인" if ci[1] < 0 else "모른다"

    r1a = _pair_diff(rows_by, "B", "G", "retrievals", "absent")
    r1p = _pair_diff(rows_by, "B", "G", "retrievals", "present")
    if r1a:
        out["R1a"] = dict(r1a, verdict=judge(r1a["ci95"], "above"))
    if r1a and r1p:
        ci = _strat_ci(list(r1a["per_task"].values()), list(r1p["per_task"].values()))
        out["R1b"] = {"diff": r1a["diff"] - r1p["diff"], "ci95": ci, "verdict": judge(ci, "above")}
    r2 = _pair_diff(rows_by, "B", "G", "input_tokens", "present")
    if r2:
        out["R2"] = dict(r2, verdict=judge(r2["ci95"], "below"))
    if "G" in rows_by:                    # R3: 있음 층 G 의 실행당 꺼냄 비율과 손익분기
        g = [r for r in rows_by["G"] if r.get("stratum") == "present"]
        p_task = list(_per_task(g, "retrievals").values())
        out["R3"] = {"p_hat": statistics.fmean(p_task) if p_task else None, "ci95": bootstrap(p_task)}
        if "B" in rows_by:
            b_in = _by_task(rows_by["B"], "input_tokens", "present")
            zero = [r["input_tokens"] - b_in[r["task"]] for r in g if r["retrievals"] == 0 and r["task"] in b_in]
            g0 = {r["task"]: r["input_tokens"] for r in g if r["retrievals"] == 0}
            per_ret = [(r["input_tokens"] - g0[r["task"]]) / r["retrievals"] for r in rows_by["G"]
                       if r["retrievals"] and r["task"] in g0]
            save, cost = (statistics.fmean(zero) if zero else None), (statistics.fmean(per_ret) if per_ret else None)
            out["R3"].update(save_per_call=save, cost_per_retrieval=cost,
                             p_star=(-save / cost) if (save is not None and cost) else None)
    r4 = _pair_diff(rows_by, "G", "H", "retrievals", "absent")
    if r4:
        out["R4"] = dict(r4, verdict=judge(r4["ci95"], "below"))
    for st in ("absent", "present"):      # R5 · 품질: 층마다 성공률 비열등(δ)
        for x, y in (("B", "G"), ("G", "H")):
            q = _pair_diff(rows_by, x, y, "success", st)
            if q:
                ok = q["ci95"] is not None and q["ci95"][0] >= -DELTA
                out[f"quality_{st}_{x}->{y}"] = dict(q, verdict="비열등" if ok else "판정 불가")
    for st in ("absent", "present"):      # R6 (기술)
        r6 = _pair_diff(rows_by, "B", "H", "input_tokens", st)
        if r6:
            out[f"R6_{st}"] = r6
    for c in ("G", "H"):                  # S4 · S7
        if c in rows_by:
            rs = rows_by[c]
            out[f"S4_{c}"] = {"pressure_high": sum(1 for r in rs if r.get("pressure_high")), "runs": len(rs),
                              "quality_known": sum(1 for r in rs if r.get("quality_known"))}
            hs = {}
            for r in rs:
                for h in r.get("retrieved_handles") or []:
                    hs[(r.get("stratum"), h)] = hs.get((r.get("stratum"), h), 0) + 1
            out[f"S7_{c}"] = {f"{k[0]}:{k[1]}": v for k, v in sorted(hs.items(), key=lambda kv: str(kv[0]))}
    return out


def _loto(a, b, metric) -> dict:
    """S1: 과업을 하나씩 빼고 짝 차의 평균을 다시 낸다. 한 과업을 뺐을 때 부호가 바뀌거나 0 이 되면 그 과업의 일이다."""
    pa, pb = _per_task(a, metric), _per_task(b, metric)
    common = sorted(set(pa) & set(pb))
    full = statistics.fmean([pb[t] - pa[t] for t in common]) if common else None
    out = {}
    for drop in common:
        d = [pb[t] - pa[t] for t in common if t != drop]
        out[drop] = statistics.fmean(d) if d else None
    sign = lambda v: (v > 0) - (v < 0)
    flips = [t for t, v in out.items() if v is not None and full is not None and sign(v) != sign(full)]
    return {"all": full, "without": out, "sign_changes_when_dropped": flips,
            "per_task": {t: pb[t] - pa[t] for t in common}}


def report_md(rep: dict) -> str:
    L = []
    if rep["not_evidence"]:
        L += ["> **이 결과는 가설의 증거가 아니다.** " + rep["not_evidence"], ""]
    L += [f"# 평가 -- {rep['tasks_file']} · 반복 {rep['reps']} · 순서 {rep.get('order', 'blocked')}"
          f"(씨앗 {rep.get('seed')}) · 배치 {rep.get('layout', 'legacy')} · fresh {rep.get('fresh', False)} · {rep['started']}",
          "", f"판본: {rep.get('versions')}", "",
          "## provider 능력(같다고 가정하지 않는다)", "", "| 자리 | 무엇 | 능력 |", "|---|---|---|"]
    for slot, cap in rep["capabilities"].items():
        L.append(f"| {slot} | {rep['slots'][slot]} | " + " · ".join(f"{k}: {v}" for k, v in cap.items()
                                                              if k not in ("provider", "model")) + " |")
    L += ["", "## 칸별", "", "| 칸 | 성공 | 고침 | 금지 실행 | 재시도 | 입력 | 캐시 안 된 입력 | 출력 | 총 토큰 | 지연 중앙(ms) | 비용 | 도구 | 꺼냄 | DENY 율 | 회복 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def f(v, k=1):
        return "-" if v is None else (f"{v:.{k}f}" if isinstance(v, float) else str(v))
    for c, s in rep["summary"].items():
        L.append(f"| {c} | {f(s['task_success'], 2)} | {s['user_corrections']} | {s['forbidden_executed']} | "
                 f"{f(s['retries'], 2)} | {f(s['input_tokens'], 0)} | {f(s.get('uncached_input_tokens'), 0)} | "
                 f"{f(s['output_tokens'], 0)} | {f(s['total_tokens'], 0)} | "
                 f"{f(s['total_ms_median'], 0)} | {f(s['cost_usd'], 4)} | {s['tool_calls']} | {s['retrievals']} | "
                 f"{f(s['arbiter_deny_rate'], 2)} | {f(s['arbiter_recovery_rate'], 2)} |")
    L += ["", "## 동작점 · 상태 읽기 (사소한 설명 S4 · S5 · S6)", "",
          f"상태 읽기: {rep.get('state_reader') or 'usage_model.snapshot'}", "",
          "| 칸 | 실행 | 압력 HIGH 계획 | DC 상태 ≠ snapshot (실행 · 상태) | 모형 |", "|---|---|---|---|---|"]
    for c, s in rep["summary"].items():
        L.append(f"| {c} | {s['runs']} | {s.get('pressure_high_runs')} | {s.get('dc_differs_from_snapshot')} "
                 f"{s.get('dc_diff_states') or ''} | "
                 f"{', '.join(s.get('models') or [])} |")
    st = rep.get("stratified")
    if st:
        L += ["", "## F2b 층별 판정 (사전등록 eval/PREREG_F2b_없음확인.md · 워밍업 " + str(rep.get("warmup_runs")) + " 실행은 뺌)", "",
              "| 판정 | 수 | 차 | 95% 구간 | 읽기 |", "|---|---|---|---|---|"]
        names = {"R1a": "없음 층 꺼냄 G−B", "R1b": "꺼냄 (없음 − 있음) 상호작용", "R2": "있음 층 입력 G−B", "R4": "없음 층 꺼냄 H−G"}
        for k, label in names.items():
            v = st.get(k)
            if v:
                ci = [round(x, 3) for x in v["ci95"]] if v.get("ci95") else None
                L.append(f"| {k} | {label} | {f(v.get('diff'), 3)} | {ci} | **{v.get('verdict')}** |")
        if st.get("R3"):
            r3 = st["R3"]
            L.append(f"| R3 | 있음 층 G 꺼냄 비율 p̂ · 손익분기 p* | p̂ {f(r3.get('p_hat'), 3)} · p* {f(r3.get('p_star'), 3)} | "
                     f"{[round(x, 3) for x in r3['ci95']] if r3.get('ci95') else None} | 절약 {f(r3.get('save_per_call'), 0)} · "
                     f"꺼냄 하나 {f(r3.get('cost_per_retrieval'), 0)} |")
        for k, v in st.items():
            if k.startswith("quality_") or k.startswith("R6_"):
                ci = [round(x, 3) for x in v["ci95"]] if v.get("ci95") else None
                L.append(f"| {k} | | {f(v.get('diff'), 3)} | {ci} | {v.get('verdict', '기술')} |")
        L.append("")
        for k in ("R1a", "R2", "R4"):
            v = st.get(k)
            if v:
                L.append(f"- S1 {k} 과업별: " + " · ".join(f"{t} {f(x, 2)}" for t, x in v["per_task"].items())
                         + f" · 하나 빼면 부호가 바뀌는 과업: {v['loto_sign_changes'] or '없음'}")
        for c in ("G", "H"):
            if st.get(f"S4_{c}"):
                s4 = st[f"S4_{c}"]
                L.append(f"- S4 {c}: 압력 HIGH {s4['pressure_high']}/{s4['runs']} · 품질 상태가 정해진 실행 {s4.get('quality_known')}/{s4['runs']}"
                         f" · S7 꺼낸 handle {st.get(f'S7_{c}')}")
    L += ["", "## 짝 비교(사전등록 판정)", ""]
    for c in rep["comparisons"]:
        q = c["quality"]
        L.append(f"### {c['pair']} -- {c['what']}")
        L.append(f"- 품질: 성공률 차 {f(q['diff'], 3)} 구간 {q['ci95']} -> **{q['verdict']}** · 안전 {'통과' if c['safety_ok'] else '**실격**'}")
        L.append(f"- 적응 칸에서 상태가 정해진 실행 {c['adaptive_state_known_runs']}/{c['adaptive_runs']} · "
                 f"압력 HIGH 계획으로 돈 실행 {c.get('adaptive_pressure_high_runs')}/{c['adaptive_runs']} (S4 동작점)")
        for m, lo in (c.get("leave_one_task_out") or {}).items():
            L.append(f"- S1 {m}: 과업별 차 " + " · ".join(f"{t} {f(v, 1)}" for t, v in lo["per_task"].items())
                     + f" · 하나 빼면 부호가 바뀌는 과업: {lo['sign_changes_when_dropped'] or '없음'}")
        for m, v in c["metrics"].items():
            k = 5 if m == "cost_usd" else 1
            L.append(f"- {m}: 고정 {f(v['fixed'], k)} -> 적응 {f(v['adaptive'], k)} · 차 {f(v['diff'], k)} 구간 "
                     f"{[round(x, k) for x in v['ci95']] if v['ci95'] else None} -> {v['verdict']} (과업 {v['tasks']})")
        L.append("")
    return "\n".join(L)


def dc_state_reader():
    """DC(cogito5170/DC)의 MSStateReader 를 과업마다 하나 짓는 공장. DC 는 선택 의존이다 -- 경로는 MS_DC_PATH(기본 ../DC).
    소스 둘: MSUsageSource(세션 상태) · MSGraphSource(세계 그래프 질의, MS 의 run_query 를 주입). 요청의 질의를 DC 가 돌리고
    (PC-23 · CMD-D13), 상태 · 질의 결과 · 기본 결정(record.default_action)을 돌려준다. MS 는 그것을 감싸 S5 의 차만 센다:
    읽을 때마다 같은 세션의 usage_model.snapshot 과 견주어 다른 상태 이름을 `diff` 에 남긴다(목적 context_runtime 은 CR 선택기가
    읽는 일곱 상태만 투영해서 tool_churn 은 DC 길에서 늘 None 이다)."""
    import os
    import subprocess
    import sys
    path = os.environ.get("MS_DC_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "DC"))
    if os.path.isdir(os.path.join(path, "dc")) and path not in sys.path:
        sys.path.insert(0, path)
    try:
        from dc import DecisionContextBuilder, MSGraphSource, MSStateReader, MSUsageSource
        from .query import StateQuery, run_query
    except ImportError as e:
        raise ImportError(f"state_reader=dc 인데 DC 를 못 읽는다({e}) -- MS_DC_PATH 에 cogito5170/DC 를 두어라") from e
    try:
        head = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        head = None

    class Reader:
        def __init__(self, usage, world):
            self.inner = MSStateReader(DecisionContextBuilder([MSUsageSource(usage, U.MODEL_VERSION),
                                                               MSGraphSource(world, run_query, StateQuery.from_dict)]),
                                       "context_runtime")
            self.diff = None

        @property
        def last(self):
            return self.inner.last

        def __call__(self, um, sid, request=None):
            out = self.inner(um, sid, request)
            snap = U.snapshot(um, sid)
            self.diff = [k for k in U.STATES if out["state"].get(k) != snap.get(k)]
            return out

    return Reader, {"kind": "dc", "path": os.path.abspath(path), "commit": head, "purpose": "context_runtime"}


def evaluate(tasks_path: str, slots: dict, configs=("A", "B", "C", "D", "E", "F"), reps: int = 3,
             provider_kw=None, order: str = "interleaved", seed: int = 0, log=print, layout: "str | None" = None,
             fresh: bool = False, state_reader: "str | None" = None, prereg: str = "eval/PREREG_적응정책.md",
             cost_limit: "float | None" = None) -> dict:
    """fresh: 실행마다 다른 표지를 시스템 글 **바로 뒤**(사용자 글 맨 앞)에 붙인다. 같은 과업을 한 시간 안에 되풀이하면
    provider 가 프롬프트 **전체**를 캐시에서 읽는다(재측정 2 의 B · D: 99.9%). 진짜 쓰임에서는 상태가 매번 달라 앞부분
    (시스템 글)만 캐시된다 -- fresh 가 그것을 흉내 낸다."""
    with open(tasks_path, encoding="utf-8") as fh:
        tf = json.load(fh)
    use = []
    for c in configs:
        if CONFIGS[c][0] not in slots:
            log(f"[{c}] {CONFIGS[c][0]} 자리가 비었다 -- 건너뛴다")
        else:
            use.append(c)
    from .prompt import DEFAULT_LAYOUT, TEMPLATE_VERSIONS
    layout = layout or DEFAULT_LAYOUT
    stamp = time.strftime("%Y%m%dT%H%M%S") if fresh else None
    log(f"칸 {', '.join(use)} · 반복 {reps} · 순서 {order}(씨앗 {seed}) · 배치 {layout} · fresh {bool(fresh)}")
    reader, reader_info = (dc_state_reader() if state_reader == "dc" else (None, None))
    if state_reader not in (None, "dc"):
        raise ValueError(f"모르는 state_reader {state_reader!r} (dc 만)")
    budget = {"limit": cost_limit, "spent": 0.0, "runs": 0, "stopped": None}
    rows_by = run_all(use, slots, tf, reps, provider_kw, order, seed, log, layout, stamp, reader, budget)
    caps = {}
    for c in use:
        slot = CONFIGS[c][0]
        pname, model = parse_slot(slots[slot])
        caps.setdefault(slot, make_provider(pname, model, **(provider_kw or {}).get(pname, {})).capabilities())
    sims = sorted({slots[CONFIGS[c][0]] for c in rows_by if any(r["simulated"] for r in rows_by[c])})
    cli = sorted({slots[CONFIGS[c][0]] for c in rows_by if slots[CONFIGS[c][0]].startswith("claude-cli")})
    note = []
    if sims:
        note.append(f"모의 provider({', '.join(sims)}) -- 토큰 · 지연 · 응답을 지어냈다. 배선 확인일 뿐이다")
    if cli:
        note.append("claude-cli 는 Claude Code 하네스가 붙어 API 의 Claude 와 같지 않다(사전등록: B · D · F 가 아니라 따로)")
    analyzed = {c: [r for r in rs if not r.get("warmup")] for c, rs in rows_by.items()}   # 워밍업은 분석에서 뺀다
    from .usage_model import MODEL_VERSION
    return {"tasks_file": tasks_path, "reps": reps, "order": order, "seed": seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "layout": layout, "fresh": bool(fresh), "state_reader": reader_info, "budget": budget,
            "slots": slots, "versions": {"usage_model": MODEL_VERSION, "tasks": tf.get("version", "datacenter-tasks-1"), "prompt_text": TEMPLATE_VERSIONS[layout]},
            "capabilities": caps, "not_evidence": " / ".join(note),
            "summary": {c: summarize(r) for c, r in analyzed.items()}, "comparisons": compare(analyzed),
            "stratified": stratified(analyzed), "warmup_runs": sum(len(r) for r in rows_by.values()) - sum(len(r) for r in analyzed.values()),
            "rows": [r for c in rows_by for r in rows_by[c]], "prereg": prereg}
