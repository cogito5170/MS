"""F2 후속(F2b) 사전등록의 '판정이 확인될 확률' -- 돌리기 전에 계산한다(BD-73).

모의 데이터는 F2(results/claude-cli_F2_2026-10-02.json)에서 잰 것만으로 짓는다. 가정은 SCENARIOS 에 다 적었다.
판정은 사전등록의 읽기와 같은 함수(ms.eval.bootstrap, 과업 단위 · 2000 번 · 씨앗 0)로 낸다.

    python3 eval/power_F2b.py            # 시나리오마다 R1a · R1b · R2 가 확인될 확률(약 4 분). 결과는 사전등록 §5 에 그대로
"""
from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, ".")
from ms.eval import bootstrap  # noqa: E402

PAIRS, REPS, SIMS = 8, 5, 400      # 사전등록한 설계: 짝 8(없음 8 · 있음 8) × 반복 5. 6 × 3 은 아래 표의 대조
# F2 에서 잰 것(B 는 이 과업들에서 꺼낸 적이 없다: t4 를 뺀 18 실행에서 0 번)
ABSENT_LIKE = {"t6": [2, 2, 2], "t3": [1, 1, 0]}        # G 의 실행당 꺼냄, '없음 확인' 과업
# 동작점마다: 꺼냄 없을 때 G−B 입력(한 호출) · 꺼냄 하나가 더하는 입력. 토큰 = 1,180 + 0.562 × 프롬프트 글자(F2 t2 의 B · G 로 맞춤)
OPERATING_POINTS = {
    "F2 그대로(서버 12 · 예산 1,500)": {"save": (-300, -250), "cost": (1060, 1773)},     # F2 실측
    "큰 세계(서버 36 · 예산 3,000)": {"save": (-830, -650), "cost": (2300, 3200)},       # 고침 1 전(B 도 요약했다 -- 버림)
    "고침 1(서버 36 · 예산 4,000 · keep_max 40)": {"save": (-1050, -850), "cost": (2600, 3400)},   # B 는 요약 없음(오프라인)
}
# 고침 1(BD-86): 칸 H = G + 요약의 덮음 선언. 없음 과업의 꺼냄을 G 에 비해 얼마나 줄이나 -- 모른다, 시나리오로
H_EFFECT = {"절반 줄임": 0.5, "1/4 줄임": 0.25, "효과 없음": 0.0}
PRESENT_P = {"F2 비율(1/9)": 1 / 9, "두 배(2/9)": 2 / 9}   # G 의 '있음' 과업 꺼냄 비율: F2 는 t2 · t5 · t7 의 9 실행 중 1
ABSENT_FRAC = {"기대": 1.0, "비관(절반만 꺼냄)": 0.5, "더 비관(1/3 만)": 1 / 3}


def _absent_profile(rng, frac, pp):
    """'없음' 과업 하나의 성질(한 번 뽑는다) -> 실행 하나의 꺼냄을 내는 함수. 같은 과업이면 칸이 달라도 같은 성질이다."""
    if rng.random() >= frac:                          # 이 '없음' 과업은 꺼내지 않는다(있음 과업처럼)
        return lambda: 1 if rng.random() < pp else 0
    pool = ABSENT_LIKE[rng.choice(sorted(ABSENT_LIKE))]
    return lambda: rng.choice(pool)


def _absent_task(rng, frac, pp):
    one = _absent_profile(rng, frac, pp)
    return [one() for _ in range(REPS)]


def _dataset(rng, frac, op, pp):
    """과업마다 G−B 의 반복 평균(꺼냄 · 입력). B 는 꺼냄 0, 입력 차는 꺼냄으로만 흔들린다(F2: 같은 과업 · 칸이면 입력 흩어짐 0)."""
    out = {"absent": [], "present": []}
    for stratum in ("absent", "present"):
        for _ in range(PAIRS):
            ks = _absent_task(rng, frac, pp) if stratum == "absent" else \
                [1 if rng.random() < pp else 0 for _ in range(REPS)]
            save, cost = rng.uniform(*op["save"]), rng.uniform(*op["cost"])
            out[stratum].append({"ret": statistics.fmean(ks), "inp": statistics.fmean(save + cost * k for k in ks)})
    return out


def _strat_diff_ci(a, b, n=2000, seed=0):
    """층화 부트스트랩: 층마다 과업을 다시 뽑아 (없음 평균 − 있음 평균)."""
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choice(a) for _ in a) - statistics.fmean(rng.choice(b) for _ in b) for _ in range(n))
    return [ms[int(0.025 * n)], ms[int(0.975 * n) - 1]]


def power_h(frac, pp, effect, sims=SIMS, seed=2):
    """R4: 없음 층에서 꺼냄 H−G 의 구간 상한 < 0 (덮음 선언이 꺼냄을 줄인다). H 의 실행마다 G 의 꺼냄을 effect 확률로 하나씩 지운다."""
    rng = random.Random(seed)
    hit = 0
    for _ in range(sims):
        diffs = []
        for _ in range(PAIRS):
            one = _absent_profile(rng, frac, pp)      # 같은 과업 -- G 와 H 가 같은 성질을 나눈다
            g = [one() for _ in range(REPS)]
            h = [sum(1 for _ in range(one()) if rng.random() >= effect) for _ in range(REPS)]
            diffs.append(statistics.fmean(h) - statistics.fmean(g))
        hit += bootstrap(diffs)[1] < 0
    return hit / sims


def power(frac, op, pp, sims=SIMS, seed=1):
    rng = random.Random(seed)
    hit = {"R1a": 0, "R1b": 0, "R2": 0}
    for _ in range(sims):
        d = _dataset(rng, frac, op, pp)
        ra = [t["ret"] for t in d["absent"]]
        rp = [t["ret"] for t in d["present"]]
        ip = [t["inp"] for t in d["present"]]
        hit["R1a"] += bootstrap(ra)[0] > 0                     # 없음 층: 꺼냄 G−B 구간이 0 위
        hit["R1b"] += _strat_diff_ci(ra, rp)[0] > 0            # 상호작용: 없음 층의 차 − 있음 층의 차 > 0
        hit["R2"] += bootstrap(ip)[1] < 0                      # 있음 층: 입력 G−B 구간이 0 밑
    return {k: v / sims for k, v in hit.items()}


if __name__ == "__main__":
    import sys as _s
    if "--amend1" in _s.argv:                         # 고침 1 의 표만(동작점 · 칸 H)
        op = OPERATING_POINTS["고침 1(서버 36 · 예산 4,000 · keep_max 40)"]
        print(f"== 고침 1 · 짝 {PAIRS} × 반복 {REPS}")
        for ppn, pp in PRESENT_P.items():
            for fn, frac in ABSENT_FRAC.items():
                p = power(frac, op, pp)
                print(f"있음 꺼냄 {ppn} | 없음 {fn}: " + " · ".join(f"{k} {v:.2f}" for k, v in p.items()))
        for hn, eff in H_EFFECT.items():
            for fn, frac in ABSENT_FRAC.items():
                print(f"R4 H {hn} | 없음 {fn}: {power_h(frac, 1 / 9, eff):.2f}")
        raise SystemExit
    design = (PAIRS, REPS)
    for pairs, reps in ((6, 3), design):
        PAIRS, REPS = pairs, reps
        print(f"== 짝 {pairs} × 반복 {reps}")
        for opn, op in list(OPERATING_POINTS.items())[:2]:
            for ppn, pp in PRESENT_P.items():
                for fn, frac in ABSENT_FRAC.items():
                    p = power(frac, op, pp)
                    print(f"{opn} | 있음 꺼냄 {ppn} | 없음 {fn}: " + " · ".join(f"{k} {v:.2f}" for k, v in p.items()))
