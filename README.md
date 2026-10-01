# MS — Model-State 층

텔레메트리와 LLM 사이에 **모형 · 상태 그래프 · 질의 · 맥락 정책 · 중재자**를 둔다. 표준 라이브러리만 쓴다.

1. **Telemetry 는 State 가 아니다.**
2. **State 의 뜻은 Model 이 정한다.**
3. **LLM 에는 State 전체가 아니라 Query 결과만 준다.**

새 방법이 아니라 묶음이다 — 왼쪽 절반은 Azure Digital Twins(DTDL 모형 · 관계 · twin graph · 질의), 오른쪽은 CaMeL · Progent 류의
결정론적 정책 집행, 가운데는 MemGPT 의 KEEP/SUMMARIZE/RETRIEVE 를 결정론적 정책으로 바꾼 것이다. [`paper/선행조사/MS.md`](paper/선행조사/MS.md)

```
TELEMETRY ─► State Manager ─► MODEL + RELATIONSHIP ─► STATE GRAPH
   ▲            manager.py       model.py                graph.py
   │                                                         │
   │                                     STATE QUERY + TOOL QUERY   query.py
   │                                                         │
   │                      CONTEXT POLICY: KEEP · SUMMARIZE · RETRIEVE   context.py
   │                                                         │
   │                               MINIMAL CONTEXT ─► LLM ─► PROPOSAL   llm.py
   │                                                         │
   │                              WALP ARBITER: ALLOW · DENY(까닭은 다음 판으로)   arbiter.py
   │                                                         │
   └──────────── 도구의 결과도 텔레메트리다 ◄──────── TOOL    tools.py · pipeline.py
```

## 원칙이 어디서 붙들리나

| 원칙 | 코드 | 시험(`tests/test_ms.py`) |
|---|---|---|
| 1. Telemetry ≠ State | 그래프에 들어가는 문은 `StateManager.ingest` 하나. 개체가 없거나 모형이 모르는 신호는 `unbound`, 타입 · 범위에 지면 `rejected`, 늦게 온 것은 `stale` — 셋 다 그래프에 안 들어가고 격리함에만 남는다. 값마다 출처(텔레메트리 id)가 붙는다 | `TelemetryIsNotState` — 도구가 돌려준 `reboot_ack` 도 모형이 모르면 상태가 아니다 |
| 2. Model 이 뜻을 정한다 | binding(신호 → 속성 + 변환) · 속성 타입/단위/범위/ttl · 파생 상태. 파생의 입력이 하나라도 없으면 default 가 아니라 **모름(None)** | `ModelDefinesMeaning` — 같은 `t=100` 이 섭씨 모형에선 critical, 화씨 모형에선 37.8°C normal |
| 3. Query 결과만 | `ContextPolicy.build` 는 `QueryResult` 만 받는다(그래프를 안 받는다). 행에는 `select` 한 속성과 **결과 안 개체끼리의** 관계만 실린다. 도구 제안도 KEEP 으로 본 개체로 좁힌다 | `OnlyQueryResultsReachLLM` — 프롬프트에 결과 밖 개체 id 가 한 글자도 없다(양성 대조 포함) · 맥락 쪽이 `graph.dump()` 를 부르면 터진다 |

## 맥락 정책

결정론적이다. 예산은 **실제로 그려지는 글자 수**(`render()`)에 건다.

- **KEEP** — 행 그대로. 질의의 `must` 에 맞는 행 · 직전에 retrieve 로 청한 행은 예산을 넘어도 싣고 `over_budget` 을 세운다(조용히 안 자른다)
- **SUMMARIZE** — 못 실은 행이 `summarize_min` 개 이상이면 개수 · 최소/최대/평균 · 값 분포(LLM 안 부름). 요약 자리를 먼저 떼어 두고 KEEP 을 채운다
- **RETRIEVE** — 못 실은 행은 늘 손잡이(`h1` …)로 남는다. LLM 이 `retrieve` 를 제안하면 중재자를 지나 다음 판에 KEEP 으로 온다

질의의 모든 행은 셋 중 정확히 하나로 간다 — 시험이 예산 200 ~ 100000 에서 붙든다.

## WALP ARBITER

LLM 이 **본 것**(최소 맥락)과 **지금 상태**(그래프)를 같이 본다. 위에서부터 처음 걸리는 것이 DENY 의 까닭:

| 규칙 | DENY 하는 때 |
|---|---|
| A0 | 제안을 못 읽었다(JSON 아님 · 모양 틀림) |
| A1 | 이 맥락에서 제안되지 않은 도구 |
| A2 | 대상을 KEEP 으로 본 적이 없다 — 요약 · 손잡이로만 봤으면 "retrieve h1 먼저" |
| A3 | 그 도구의 대상 목록에 없다 |
| A4 | 인자가 도구의 params 와 안 맞는다 |
| A5 | 맥락을 지은 뒤 대상의 상태가 바뀌었다(본 판 ≠ 지금 판) |
| A6 | 사전조건이 보는 속성이 없거나 ttl 을 넘겼다 · 사전조건이 지금 거짓 |
| A7 | `external` · `irreversible` 인데 허가(`--grant`)가 없다 — 기본 DENY |
| A8 | 같은 대상 · 같은 판에서 이미 ALLOW 한 같은 제안 |
| E | 중재자 안의 예외 — **DENY**(닫힌 쪽으로) |

DENY 의 까닭은 다음 판 맥락의 `denied` 로 LLM 에 돌아간다. `tool: "none"` 은 NOOP. 판정은 `--ledger` 로 JSONL 에 남는다.
walp 실행 정책층 설계 §5 의 안전 불변식 1~3 을 이 자리에 옮긴 것이다. 다만 walp 훅은 터지면 **안 막는 쪽**(사람의 말을 잃지 않게)이고,
여기는 터지면 **막는 쪽**이다 — 지키는 것이 다르다(검사 안 된 도구 실행을 안 한다).

## 돌리기

```bash
python3 -m ms demo                      # 예시 세계(서버 12 · 랙 2) + 대본 LLM -- A2 · A7 에 막히고 셋째 판에 ALLOW
python3 -m ms demo --llm "claude -p"    # 같은 세계, 진짜 모형 (gemini -p 도 된다)
python3 -m ms ingest  ms/examples/datacenter.json --telemetry ms/examples/datacenter_telemetry.jsonl
python3 -m ms context ms/examples/datacenter.json --telemetry ms/examples/datacenter_telemetry.jsonl --task "과열 서버"
python3 -m ms run     SPEC.json --telemetry T.jsonl --task "..." --llm "claude -p" --grant reboot --ledger arb.jsonl
python3 -m unittest tests.test_ms       # 46 개
```

SPEC 꼴은 [`ms/examples/datacenter.json`](ms/examples/datacenter.json) — `models · relationships · entities · edges · tools · queries · policy · grants · now`.
코드에서는:

```python
from ms import StateManager, ToolRegistry, Pipeline, ContextPolicy, WalpArbiter, CommandLLM

m = StateManager.from_spec(spec)
m.ingest({"source": "bmc", "entity": "srv07", "signal": "cpu_temp_f", "value": 197.6, "ts": 992})
reg = ToolRegistry(spec["tools"]); reg.bind("throttle", lambda target, args: [{"signal": "throttle_ack", "value": True}])
res = Pipeline(m, reg, CommandLLM(["claude", "-p"]), ContextPolicy(1500), WalpArbiter(reg, grants={"reboot"})) \
        .run("과열된 서버를 처리하라", spec["queries"])
```

## 확인한 것

- 시험 46 개 통과. **시험이 헛돌지 않는지** 코드를 일부러 망가뜨려 봤다 — A2 · A5 · A7 제거, 중재자 예외를 ALLOW 로,
  `select` 무시, 결과 밖 관계 누설, 모르는 신호를 상태로, 늦은 관측 덮어쓰기, 모름을 default 로, 줄인 행 조용히 버리기,
  안 본 개체에 도구 제안 — 11 가지 모두 빨개진다. (마지막 것은 처음엔 **살아남았다** — 그 시험에서는 도구를 쓸 수 있는 개체가
  마침 전부 보이고 있었다. 안 보이는 대상이 있는지부터 확인하도록 고쳤다.)
- 배선: `--llm "claude -p"` 로 예시를 한 번 돌렸다(2026-10-01). 모형이 `throttle srv07 level 3` 을 냈고 ALLOW, 결과가 텔레메트리로 들어가
  `throttled=True` 가 됐다. **한 번이다 — 잰 것이 아니다.**

## 알고 쓸 것

- **잰 것이 없다.** 토큰을 얼마나 아끼는지, 막아야 할 제안을 얼마나 막는지, 맥락을 줄여서 LLM 의 판단이 나빠지는지 모른다.
  예시의 "16 행 → KEEP 6 · 요약 6 · 손잡이 2" 는 장난감 세계 하나의 산수다.
- 예산은 **글자 수**다(토큰이 아니다). 한글 · JSON 은 글자당 토큰이 다르다.
- SUMMARIZE 는 개수 · 최소/최대/평균 · 값 분포뿐이다. 요약에 안 보이는 이상치(예: 6 개 중 1 개의 온도가 빠짐)는 `n` 으로만 드러난다.
- 중재자가 보는 것은 **근거 · 신선도 · 사전조건 · 허가**다. 제안이 **옳은지**(그 서버를 낮추는 것이 맞는 처방인지)는 안 본다.
  사전조건을 모형에 잘 적는 것이 그 몫이다.
- 한 판에 제안 하나. 스레드 안전하지 않다. 그래프는 메모리에만 있다(저장 · 복원 없음).
- [Sensor](https://github.com/cogito5170/Sensor) 의 판독 · [walp](https://github.com/cogito5170/walp) 의 실행 정책과는 **아직 안 이었다.**
  llmsensor 의 판독(OK · SUSPECT · FAULT · UNKNOWN)은 그대로 텔레메트리로 넣을 수 있는 꼴이다.
