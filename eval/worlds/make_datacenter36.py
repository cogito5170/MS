"""F2b 의 큰 세계: 기본 데이터센터(서버 12 · 랙 2)에 정상 서버 24 대(srv13~srv36)를 더한다. 결정론적(씨앗 7).

    python3 eval/worlds/make_datacenter36.py      # datacenter36.json · datacenter36_telemetry.jsonl 을 다시 쓴다

더한 서버는 모두 정상이다(온도 55~68 ℃, 팬 ok) -- 어느 과업의 목표도 아니다. srv13~24 는 rack1, srv25~36 은 rack2 에 넣는다.
사전등록: eval/PREREG_F2b_없음확인.md 고침 1. 생성한 파일을 커밋하고, 시험이 다시 지어 바이트까지 같은지 본다.
"""
from __future__ import annotations

import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BASE_SPEC = os.path.join(ROOT, "ms", "examples", "datacenter.json")
BASE_TEL = os.path.join(ROOT, "ms", "examples", "datacenter_telemetry.jsonl")
SEED, EXTRA = 7, 24


def build() -> "tuple[str, str]":
    with open(BASE_SPEC, encoding="utf-8") as f:
        spec = json.load(f)
    with open(BASE_TEL, encoding="utf-8") as f:
        tel = [line for line in f.read().splitlines() if line.strip()]
    rng = random.Random(SEED)
    for i in range(13, 13 + EXTRA):
        nid = f"srv{i:02d}"
        spec["entities"].append({"id": nid, "model": "Server"})
        spec["edges"].append(["contains", "rack1" if i <= 24 else "rack2", nid])
        tel.append(json.dumps({"source": "bmc", "entity": nid, "signal": "cpu_temp", "value": rng.randint(55, 68), "ts": 990}))
        tel.append(json.dumps({"source": "bmc", "entity": nid, "signal": "fan_state", "value": "ok", "ts": 980}))
    return json.dumps(spec, ensure_ascii=False, indent=1) + "\n", "\n".join(tel) + "\n"


if __name__ == "__main__":
    s, t = build()
    with open(os.path.join(HERE, "datacenter36.json"), "w", encoding="utf-8") as f:
        f.write(s)
    with open(os.path.join(HERE, "datacenter36_telemetry.jsonl"), "w", encoding="utf-8") as f:
        f.write(t)
    print("datacenter36.json · datacenter36_telemetry.jsonl")
