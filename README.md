# MS — LLM Policy Runtime

**MS 는 LLM provider 가 아니다.** OpenAI · Claude · Gemini 는 추론 provider 이고, MS 는 그 **위에서** 도는 정책 런타임(control plane)이다.
MS 가 정하는 것은 넷이다: LLM 에게 **무엇을 보일지**(Context Policy), **어떻게 말할지**(Prompt Policy), **누구에게 물을지**(Provider Policy),
LLM 의 제안을 **받을지**(Arbiter · 앞으로 Validate · Arbitrate · Guard). 표준 라이브러리만 쓴다 — provider SDK 도 안 쓴다.

**연구 대상은 이 적응 고리다:**

```
Telemetry → State → CR(맥락) → Provider → LLM → Arbiter
    ▲                                                          │
    └────────────── 실행 결과도 Telemetry 로 돌아온다 ◄──────────┘
```

> 가설(사전등록 `eval/PREREG_적응정책.md`): 텔레메트리에서 나온 State 로 Context Policy 와 Prompt Policy 를 동적으로 고르면,
> 고정 정책보다 **품질을 지키면서** 토큰 · 지연 · 재시도 · 비용을 줄이는가? — 안전과 최소 품질은 단단한 제약이다.
> **아직 답이 없다** (아래 "잰 것").

> **WALP 는 쓰지 않는다(2026-10-02).** 처음 설계 그림의 "WALP ARBITER" 는 지금의 `Arbiter` 다. 옛 사전등록 · 결과 파일의
> `walp_*` 칸은 그때의 기록이라 그대로 둔다(지금 텔레메트리는 `arbiter_*`, ms-run-telemetry-2).
>
> **이름 고침(2026-10-02)**: 아래에서 Context Policy · Prompt Policy 라고 부르는 것은 정책이 아니라 **Context Runtime(CR)** 이다 —
> 매 요청 무엇을 보이고 어떻게 말할지 정하는 런타임이고, 그 결정을 Context Decision(CD)이라 부른다. 코드는 `ms/cr.py` 한 곳으로 모였다.
> 지금은 에이전트가 MS 안에서 맡고 나중에 독립 계층으로 옮긴다. 역할 기록 [`docs/역할.md`](docs/역할.md) · 앞으로의 계획 [`docs/계획.md`](docs/계획.md).

> **Sensor 배선(②, 2026-10-02)**: `ms/sensing.py` 가 llmsensor 의 `sense()` 출력을 텔레메트리 신호로만 편다 -- 측정 · 관측 · 잔차는
> 수만 받고, Sensor 의 추정(Q) · 판정은 받지 않는다. `pip install ".[sensor]"` 로 Sensor 를 함께 깔 수 있다(선택).
> **질의도 결정 문맥을 거친다(PC-23, cr-2)**: `state_reader` 가 인자를 셋 받으면 요청을 넘기고, 리더가 질의 결과(`queries`)를 돌려주면
> CR 은 그래프에 직접 묻지 않고 그것으로 맥락을 짓는다. **낡은 값은 LLM 에 안 간다**(BD-65): 값은 null · `_unusable`. 질의에
> `allow_stale: true` 를 명시할 때만 값과 `_stale` 표시를 보인다(지금 그렇게 하는 질의는 없다).
> **cr-3 (BD-85 · BD-76)**: LLM 에 보이는 속성 순서는 CR 이 정한다(질의의 select 순, 없으면 모형 선언 순) -- DC 길과 직접 길이 같은
> 글자열을 낸다(시험). 꺼냄은 두 길 모두 이번 요청의 질의 결과 안에서만. 결정 문맥이 준 기본 결정(`record.default_action`, KEEP)은
> 선택기가 필수 상태를 몰라 규칙을 못 정할 때 쓴다.
> **BD-46 으로 대체 예정**: baseline 결정으로 Sensor 상태는 Sensor state-export → DC → `Runtime(state_reader=…)` 한 길로만 온다.
> `sensing.py` 는 더 넓히지 않고, 그 길이 확인되면 걷어 낸다. CR 의 자리(BD-21)와 Validate · Arbitrate · Guard 배분(BD-24)은 `docs/계획.md`.

## 세 가지를 섞지 않는다

| | 묻는 것 | MS 에서 | 예 |
|---|---|---|---|
| **Telemetry** | 무슨 일이 일어났는가 | `Telemetry` · `RunRecord` — 뜻이 없는 기록. 그래프에 바로 못 들어간다 | `input_tokens = 18000` · `cpu_temp_f = 197.6` |
| **State** | Model 에 의해 지금 무엇을 의미하는가 | `Model` 이 해석해 `StateGraph` 에 둔 것. 원 측정(측정 창, `measurement`)은 그래프 밖 | `token_budget_pressure = HIGH` · `srv07.status = critical` |
| **Policy** | 지금 State 에서 무엇을 할 것인가 | `policy.py` 의 선택기 — (State, 판본)의 순수 함수 | 예산 ×0.5 · COMPRESS · 지시 concise |

`input_tokens / token_budget ≥ 0.9` 를 `HIGH` 로 읽는 것은 **모형**이다(`usage_model.py`). 토큰 수 자체는 상태 그래프에 **없다** —
모형의 측정 창(`measurement`, 옛 이름 evidence)에만 있고, 질의로도(→ LLM 으로도) 안 보인다.

## 구조

```
USER
 ↓
MS API                 runtime.Runtime.handle            요청 하나 = 정책 루프 한 번
 ↓
Request Manager        runtime.Runtime                   세션 상태를 읽고 정책 셋을 고르고, 실행을 RunRecord 로 적는다
                       (state_reader)                    상태 읽기의 이음매 -- 기본 usage_model.snapshot, cogito5170/DC 의 MSStateReader 를 꽂을 수 있다
 ↓
State Query            query.run_query · tool_query      그래프에서 LLM 쪽으로 나가는 유일한 길
 ↓
Context Policy         context.ContextPolicy             KEEP · SUMMARIZE · RETRIEVE · DROP · DEFER · COMPRESS
 ↓                     policy.AdaptiveContext            ← State
Prompt Policy          prompt.PromptPolicy               지시 꼴 · 맥락 꼴 · 예시 · 추론 설정 · 출력 꼴 · 도구 허용(좁히기만)
 ↓                     policy.AdaptivePrompt             ← State
Provider Policy        policy.ExplicitProvider           지금은 명시 선택만
 ↓
Provider Adapter       providers/                        CanonicalRequest → provider 요청, provider 응답 → CanonicalResponse
 ├── OpenAI            providers/openai.py               Responses API
 ├── Claude            providers/claude.py               Messages API      (claude_cli.py: `claude -p`, API 와 같지 않음)
 └── Gemini            providers/gemini.py               generateContent
 ↓
LLM
 ↓
Proposal               llm.proposal_from                 글 속 JSON 이든 함수 호출이든 **제안**일 뿐이다
 ↓
Arbiter                arbiter.Arbiter                   ALLOW · DENY (A0~A8, 예외면 DENY)
 ↓
Tool                   tools.ToolSpec.run                ALLOW 가지 안에서만 불린다(호출 자리가 하나)
 ↓
Telemetry              run_telemetry.RunRecord           정규 텔레메트리 + 도구의 결과
 ↓
State Manager          manager.StateManager              Model 이 해석 → 다음 State
```

## 계층의 책임과 경계

| 계층 | 하는 일 | **못 하는 일** (시험이 붙든다) |
|---|---|---|
| State Manager | 텔레메트리를 모형으로 해석해 그래프에. 모르는 신호 · 범위 밖 · 늦은 관측은 격리 | 모형 없이 상태를 만들기 |
| Model (`model.py` · `usage_model.py`) | 신호 → 속성(변환 · 단위 · 범위 · ttl), 속성 → 파생 상태, 측정 창 `measurement`(window · agg) | 입력이 없는데 기본값으로 메우기 — 모르면 **모름** |
| State Query | 고른 속성 · 결과 안 개체끼리의 관계만 | 그래프 전체를 내기 |
| Context Policy | 질의 결과 → 최소 맥락. 예산은 **실제로 그려진 글자 수**에 | must 행을 빼거나 미루기 · 행을 조용히 버리기 |
| Prompt Policy | 맥락을 어떻게 말할지 | 도구를 **넓히기**(좁히기만 된다) · 중재자를 바꾸기(import 조차 안 한다) |
| Provider Policy | provider · 모형 고르기 | 승자를 가정하기 — 지금은 명시 선택뿐 |
| Provider Adapter | canonical ↔ provider 번역, 사용량 정규화, provider 고유 값은 `extensions` 로 | 위 계층에 provider 모양을 흘리기 · 못 하는 옵션을 흉내 내기(`unsupported` 로 적는다) |
| Arbiter | LLM 이 본 것 + 지금 상태로 ALLOW / DENY | 프롬프트 · LLM 의 말로 허가가 바뀌기 |
| Tool | 세계에 작용하고 **관측**을 돌려준다 | 상태를 직접 쓰기 — 결과는 텔레메트리로 다시 들어간다 |

## 원칙 열 개와 그것을 붙드는 시험

| # | 원칙 | 시험 (`tests/test_runtime.py`, `tests/test_ms.py`) |
|---|---|---|
| 1-3 | MS 는 provider 가 아니다 · provider 는 추론만 · MS 는 그 위의 정책 층 | `AdapterIsolation` — 위 계층은 등록부(`make_provider`)만 import, SDK import 없음 |
| 4 | LLM 은 State Graph 전체에 접근하지 않는다 | `LLMSeesOnlyQueries.test_request_holds_no_graph_objects` — 요청 안에 그래프 · 노드 객체가 없고 기본 타입뿐 |
| 5 | Query · Context Policy 가 허락한 최소 맥락만 | `test_only_query_authorized_entities` (양성 대조 포함, 세션 이름 · 예산 · 상태 이름도 안 보임) · `test_dropped_rows_not_sent` |
| 6 | LLM 의 출력은 Proposal | `ProposalBoundary.test_native_tool_call_is_only_a_proposal` — 함수 호출 응답도 도구를 안 부른다 |
| 7 | 실행 여부는 Arbiter 가 | `test_deny_means_no_tool_whatever_the_llm_says` · `test_single_call_site` · `PromptCannotOverrideArbiter` |
| 8 | 모든 provider 를 같은 텔레메트리 꼴로 | `Normalization.test_same_work_same_canonical_usage` — 같은 일을 세 provider 가 제 말투로 보고해도 canonical 이 같다 |
| 9 | 원 텔레메트리 ≠ 의미 있는 State | `test_raw_counts_are_not_state` — 토큰 수는 그래프 · 질의에 없고 파생 상태만 있다 |
| 10 | provider 모양이 State · Policy 모형에 안 스민다 | `test_provider_field_names_stay_in_adapters` · `ProviderCannotChangeSemantics` |
| — | 정책 결정은 기록된 State + 판본으로 재현된다 | `Reproducible` — `replay(결정 기록)` 가 맞고, 기록을 고치면(상태 · 판본) 잡는다. 실행 기록은 결정을 id 로만 가리킨다 |

**시험이 헛돌지 않는지** 코드를 일부러 망가뜨려 봤다(2026-10-01). 새 불변식 12 가지 — Claude 캐시를 입력에 안 더함 · Gemini 사고 토큰 뺌 ·
Gemini 추론 옵션 흉내 · 비스트리밍 TTFT 지어냄 · 확장을 신호로 · 원 측정을 그래프에 · 품질 우선 끔 · 도구 좁히기 안 함 · 중재 전에 도구 ·
요청에 그래프 통째 · 정책이 기록과 다른 상태를 봄 · must 질의도 미룸 — 모두 빨개진다. 무해 대조 하나는 초록으로 남는다.
처음 만든 MS 의 불변식 11 가지도 그대로다.

## Provider — 같다고 가정하지 않는다

canonical `input_tokens` 는 OpenTelemetry GenAI 규약대로 **캐시 읽기를 포함한** 전체 입력, `output_tokens` 는 **추론 · 사고를 포함한** 청구 기준이다.
그래서 어댑터마다 하는 일이 다르다:

| | OpenAI Responses | Claude Messages | Gemini generateContent | claude-cli |
|---|---|---|---|---|
| 입력 | 그대로(캐시 포함) | `input + cache_read + cache_creation` (Claude 는 캐시를 뺀 몫을 보고) | `prompt + toolUsePrompt` | Claude 와 같음 |
| 출력 | 그대로(reasoning 포함) | 그대로(사고 포함) | `candidates + thoughts` (Gemini 는 사고를 뺀 몫을 보고) | Claude 와 같음 |
| JSON 스키마 | native (`text.format`, strict) | native (`output_config.format`) | MIME 만, 스키마는 프롬프트로(필드 이름 미확인) → `unsupported` | 프롬프트로 → `unsupported` |
| 추론 조절 | `reasoning.effort` — 모형에 달림 | `output_config.effort`. Haiku 4.5 는 거절, `off` 는 어디서도 안 함 | Gemini 3 의 `thinkingLevel` low/high 만 | `--effort` |
| TTFT | 스트리밍에서만 | 스트리밍에서만 | 스트리밍에서만 | 없음 |
| 비용 | 가격표(사용자가 줌) | 가격표 | 가격표 | **보고함** (`total_cost_usd`) |
| 여기서 돌렸나 | **못 돌렸다** — 키 없음, 프록시가 막음 | 키 없음 — 녹음 응답으로만 | 키 없음 — 녹음 응답으로만 | **돌렸다** |

provider 고유 값(Claude 의 `cache_creation_input_tokens`, OpenAI 의 `reasoning_tokens`, Gemini 의 `thoughtsTokenCount` …)은 `extensions.<provider>`
에 담긴다. **신호로 펴지 않는다** — 그래서 State 의 뜻을 바꿀 수 없다.

## 정규 텔레메트리 (`run_telemetry.RunRecord`)

```
run          run_id · session_id · provider · model · timestamp · simulated
tokens       input_tokens · output_tokens · cached_input_tokens · context_tokens* · retrieved_tokens* · total_tokens
latency      ttft_ms (스트리밍일 때만) · inference_ms · total_ms
interaction  llm_calls · tool_calls · retries · context_retrievals · arbiter_denies · proposal_invalid · non_progress_rounds
outcome      task_success (Runtime 은 채우지 않는다 -- 평가 하니스가 그래프로 판정해 evaluation() 으로 돌려준다, PC-13) · user_correction (피드백으로) · tool_success
decision_ref 이 실행을 낸 결정 기록의 id ← 결정의 내용은 여기 없다(아래)
cost         usd · source (provider | price_table | None)
estimated    * 추정한 칸과 방법        unsupported  못 해서 안 보낸 옵션        extensions  provider 고유 값
```

`otel()` 이 OpenTelemetry GenAI 이름(`gen_ai.usage.input_tokens` 등)으로도 낸다.

**L0 Telemetry 로도 낸다 (`ms/l0.py`, 선택 의존 · 2026-10-02).** [cogito5170/Telemetry](https://github.com/cogito5170/Telemetry) 가 깔려 있고
`Runtime(..., l0_ledger="l0.jsonl")`(또는 `ms ask … --l0-ledger l0.jsonl`)을 주면, 실행마다 `run.start` · 모형 호출마다 `llm.request → llm.response | llm.error` ·
도구 호출마다 `tool.start → tool.end` · `run.end`(끝난 까닭 · 회전 수 · 벽시계 · provider 보고 비용 · `decision_ref`)를 L0 원장에 덧붙인다.
계측 자리는 둘뿐이다 -- `Pipeline._call`(provider 를 부르는 유일한 자리)과 ALLOW 가지의 도구 호출(도구를 부르는 유일한 자리).
**L0 에 가지 않는 것**: 정책이 본 상태 · 계획 · 중재 결정(ALLOW/DENY · 규칙) · 맥락 글 · 겨냥 글 · 예외 메시지 · 가격표 비용 · 추정 토큰.
사용량은 canonical(OTel 꼴) 그대로 `total_input_tokens` 로 -- 캐시 밖 입력을 지어내지 않는다. 같은 원장을 Sensor 가 `telemetry.compat` 으로 읽는다.
L0 가 없으면 아무것도 안 내고 그대로 돈다. 달라고 했는데 없으면 `ImportError` 다(조용히 안 버린다).

**결정 기록은 텔레메트리와 따로다 (`decision_record.DecisionRecord`, ms-run-telemetry-3 · 2026-10-02).** 정책이 본 상태 · 맥락 / 프롬프트 /
provider 계획 · 중재 결정 · 재현 입력은 `DecisionRecord` 에 있고, id 는 내용의 sha256 이다. 원장에는 `{"kind": "decision"}` 줄이 먼저,
그 결정이 낳은 `{"kind": "run"}` 줄이 `decision_ref` 로 그것을 가리킨다(`linked()` -- 결정 기록을 고치면 끊긴다). `replay()` 는 결정 기록을
받는다. 까닭: 텔레메트리는 "무슨 일이 일어났나" 만 적는다 -- [L0 Telemetry](https://github.com/cogito5170/Telemetry) docs/TELEMETRY.md 6 · 7 절.
도구를 실행하면 결정 기록은 그 **직전**에 지어진다(PC-19 G1). id 의 입력은 그때 이미 다 정해져 있어서 id 는 그대로다.

**ActionIntent 를 따로 기록한다 (`ms/intent.py`, shadow · 선택 의존 · CMD-M15 · BD-97).** [cogito5170/action](https://github.com/cogito5170/action)
의 꼴 `action-contract/1` 이 import 되고 상태를 **DC 로 읽을 때만**(결정 문맥 id 가 있을 때) 낸다.
- 의도의 칸은 dc_id · `policy="ms-cr@cr-3"` · `author_kind="llm"` · used_keys(`query:<질의 이름>`, BD-100) 다.
- 의도는 실행기로 갈 도구 행동뿐이다. BD-76 기본 결정(KEEP)은 CR 안의 맥락 결정이라 의도가 아니다(BD-100).
- 원장에는 `{"kind": "intent", "decision_ref", "round", "intent"}` 줄로 남고, `handle()` 의 `intents` 에도 담긴다.
- error · none · retrieve 는 의도가 아니다.
- `Proposal` · 중재 · 결정 기록은 그대로다. 의도는 결정 기록에 넣지 않아서 결정 id 가 안 바뀐다.
- A8 되풀이 열쇠는 `Proposal.key()` 그대로다. intent_id 를 열쇠로 쓰면 까닭만 바꾼 되풀이를 놓친다.
- 시험은 action 이 옆(`MS_ACTION_PATH`, 기본 `../action`)에 있을 때만 돈다.

**Guard 를 Arbiter 옆에서 shadow 로 부른다 (`ms/guard_shadow.py`, 선택 의존 · CMD-M17 · guard F4).** [cogito5170/guard](https://github.com/cogito5170/guard)
(`guard-result/1`)가 import 되고 의도가 있을 때(DC 배선)만 돈다.
- 판마다 Arbiter 판정 **직후**(실행 전), 같은 지금 상태로 `guard.evaluate` 를 부른다.
- 입력은 셋이다.
  - DCView: DC 문맥 `to_dict()` · 목적 명세를 `dcview_from_dc` 에 넘긴다. 둘 다 상태를 읽은 직후에 붙잡는다. 그 판의 CR 맥락이 내놓은 것(offers)과 본 것(seen)도 함께 넘긴다.
  - StateView: State Manager 의 지금 상태다.
  - GuardModel: ToolSpec 과 grants 다.
- A8 기억은 Runtime 이 든다.
- **실행은 Arbiter 가 정한다.** Guard 결과는 원장 `{"kind": "guard", "decision_ref", "round", "intent_id", "arbiter", "guard"}` 줄과
  `handle()` 의 `guards` 에만 남는다. 결정 기록에는 넣지 않으므로 결정 id 가 그대로다.
- 어댑터 · 재료 오류는 DENY(E) 로 남긴다.
- 대조표: `python3 eval/guard_contrast.py`. 시험은 `MS_GUARD_PATH`(기본 `../guard`)에 guard 가 있을 때만 돈다.

**DC 길의 도구 실행은 실행기가 한다 (`ms/dispatch.py`, CMD-M20 shadow → M21 → M22 execute · BD-108 (4) · BD-111).** 한 실행은 한 사건이다(BD-97 Q3).
- DC 길(결정 문맥 id 가 있는 길)에서 도구를 실행하면 `action.executor.execute(cmd, ActionModel, {이름: ms_handler(tool.run)}, recorder, mode="execute")` 로 한다.
  그 실행(run)의 L0 에는 `action.dispatch` / `action.result` 한 쌍만 남는다(`action_ref` = command_id, 인자는 `args_sig` 로). `tool.*` 은 없다.
- **snapshot 길은 그대로** `tool.run` · `tool.start` / `tool.end` 다.
- ActionCommand 의 재료는 셋이다.
  - **Arbiter 가 ALLOW 한 의도**의 `intent_id · action · target · args`. E3(Guard enforce) 전까지는 실행을 정하는 쪽의 판정에서 짓는다(BD-111)
  - `before_execute` 가 Pipeline 에 돌려준 결정 id
  - `issued_at` = 시계 × 1000(ms)
- 관측: 처리기가 돌려준 관측을 지금처럼 ingest 한다. 처리기가 던지면 지금과 같은 `tool_error` 관측(메시지까지)이고, L0 에는 예외 종류 이름만 간다.
- 명령을 지을 수 없거나(의도 없음 등) 실행기가 거절하면 지금 길로 실행한다. 실행을 잃지 않고, 까닭은 원장 줄의 `fallback` 에 남는다.
- Guard 결과는 옆에 기록만 한다. 둘 다 ALLOW 면 재료가 guard `command_material` 과 같은지 `material_vs_guard` 에 적는다.
- 원장에는 `{"kind": "execution", "decision_ref", "round", "model", "command", "execution"[, "fallback"]}` 줄로 남고, `handle()` 의 `executions` 에도 담긴다.
- 대조: `python3 eval/sensor_contrast.py`(바꾸기 전후의 L0 를 Sensor 에 넣어 `execution_health` 등을 견준다) · `python3 eval/executor_contrast.py`.

**도구 명세 · 술어 · 인자 검사는 action 의 한 벌이다 (BD-108 · BD-111).** action(`action-contract`)은 **필수 의존**이고 pyproject 에
커밋 sha 로 고정한다(guard · health 와 같은 sha).
- `ToolRegistry` 는 도구 정의(JSON)를 `ActionSpec.from_tool` 로 읽는다. ToolSpec 은 그 투영(`to_ms_tool`)에 MS 실행 쪽 결합(handler ·
  effect)만 붙인 것이다. `ToolRegistry.model` 이 ActionModel 이다. retrieve 는 CR 안의 일이라 들지 않는다.
- `ms/predicate.py` 는 `action.predicate` 를 다시 내보내는 얇은 층이다(`props_of` 만 집합으로 감싼다). `ToolSpec.check_args` 는
  `action.params.check_args` 다.
- action 이 없으면 `import ms` 가 `ImportError` 로 멈춘다. 다른 벌로 조용히 가지 않는다.

## State — 사용의 모형 (`usage_model.py`)

세션 하나의 원 측정은 전부 측정 창(`measurement`)이고, 그래프에는 여덟 상태만 산다. **문턱은 잰 것이 아니라 손으로 둔 것이다** (`MODEL_VERSION`).
예산(`token_budget` · `context_budget` · `latency_budget_ms`)은 **관측이 아니라 운영자 설정**이다(usage-model-4, PC-03): `open_session` 이
`configure()` 로 정하고 텔레메트리로는 못 바꾼다(`config.*` 신호는 unbound). 파생 상태의 시각은 관측 입력만으로 정한다.

| 상태 | 모형의 해석 |
|---|---|
| `token_budget_pressure` | 마지막 실행 input_tokens ≥ 0.9 × token_budget → HIGH, ≥ 0.6× → MEDIUM |
| `context_pressure` | 마지막 실행 context_tokens ≥ 0.9 · 0.6 × context_budget |
| `latency_pressure` | 최근 5 실행 total_ms 평균 ≥ 1.0 · 0.7 × latency_budget_ms |
| `task_complexity` | 질의 행 ≥ 40 · 10, 또는 최근 5 실행 LLM 호출 ≥ 3 · 2 |
| `answer_reliability` | 최근 5 실행: 못 읽은 제안 ≥ 0.2 이거나 DENY ≥ 호출의 절반 → LOW / ≤ 0.05 이고 ≤ 0.1 → HIGH |
| `correction_rate` | 최근 10 피드백의 사용자 고침 비율 ≥ 0.2 · 0.05 |
| `retry_pressure` | 최근 5 실행 재시도 평균 ≥ 1 · 0.3 |
| `tool_churn` | 최근 5 실행 진전 없는 판(DENY · RETRIEVE) 평균 ≥ 2 · 1 |

입력이 없으면 상태는 **모름**이고, 정책은 모름을 "고정 정책대로" 로 읽는다.

## Policy

**품질 · 안전이 먼저다.** `answer_reliability=LOW` 나 `correction_rate=HIGH` 면 적응 맥락은 **아무것도 줄이지 않고**, 적응 프롬프트는
예시 2 · JSON 스키마 · 칸 설명을 붙여 오히려 더 쓴다. 토큰을 아끼려고 품질을 깎는 계획은 나오지 않는다.

- **Context** (`ctx-adaptive-1`): 압력 HIGH → 예산 ×0.5 · COMPRESS · DROP · 우선순위 ≥ 2 질의 DEFER / MEDIUM → ×0.75 · COMPRESS /
  task_complexity HIGH 면 DROP · DEFER 를 끄고 ×0.75 밑으로 안 내린다. must 행은 어떤 동작에도 빠지지 않는다.
- **Prompt** (`prompt-adaptive-1`): 압력 HIGH → 지시 concise / latency HIGH → reasoning low · 출력 512 / complexity HIGH → reasoning high /
  correction HIGH → 도구를 `no_irreversible` 로 **좁힌다**.
- **Provider** (`provider-explicit-1`): 요청이 이름 댄 것. `ProviderPolicy.select(state, request)` 인터페이스만 있고, 상태로 고르는 정책은
  같은 과업을 provider 둘에 돌린 평가가 쌓인 뒤의 일이다.

## Arbiter

LLM 이 **본 것**(최소 맥락)과 **지금 상태**(그래프)를 같이 본다: A0 못 읽음 · A1 제안 안 된 도구 · A2 본 적 없는 대상(요약 · 미룸이면
"retrieve 먼저", DROP 이면 "정책이 뺐다") · A3 그 도구의 대상 아님 · A4 인자 · A5 맥락 뒤 상태 바뀜 · A6 사전조건 낡음/거짓 ·
A7 external · irreversible 허가 없음 · A8 같은 판 되풀이 · E 예외면 DENY. DENY 의 까닭은 다음 판 맥락으로 LLM 에 돌아간다.

## 평가 (`ms/eval.py`)

| 칸 | provider | Context | Prompt |
|---|---|---|---|
| A · B | OpenAI · Claude | fixed | fixed |
| C · D | OpenAI · Claude | adaptive | fixed |
| E · F | OpenAI · Claude | adaptive | adaptive |

같은 과업(`eval/tasks/datacenter.json`, 7 개) · 같은 성공 기준(최종 **상태 그래프**에 대한 술어 + 금지 도구 미실행) · 같은 Arbiter 허가.
과업마다 세계를 새로 짓고, 세션 상태는 이어 간다. 실패하면 모의 사용자가 한 번 고친다(user_correction). 짝(A↔C · B↔D · C↔E · D↔F)마다
과업 단위 부트스트랩 95% 구간 · 품질 비열등(δ=0.05) · 안전을 먼저 본다. **provider 사이는 비교하지 않는다.**
잰 것: 성공 · 고침 · 재시도 · 입력/출력/총 토큰 · 지연 · 비용 · 도구 호출 · 꺼냄 · Arbiter DENY 율 · 회복률.

```bash
python3 -m ms eval --tasks eval/tasks/datacenter.json \
    --openai openai:<모형> --claude claude:claude-opus-5-5 --reps 3 --out eval/results/<이름>    # 키: OPENAI_API_KEY · ANTHROPIC_API_KEY
```

### 잰 것 — 아직 가설의 답이 아니다

| 무엇 | 결과 | 읽는 법 |
|---|---|---|
| 모의 provider(`sim-*`) A~F | 배선이 끝까지 돈다 | **증거 아님.** 토큰 · 지연을 지어냈다. 모의 에이전트가 7 중 5 를 틀려 correction_rate=HIGH → 적응 정책이 품질 우선으로 **줄이지 않았다**(규칙대로) |
| `claude-cli` B · D · F, 반복 1 (2026-10-01, 약 $0.29, 3 분) — [`eval/results/claude-cli_배선_2026-10-01.md`](eval/results/claude-cli_배선_2026-10-01.md) | 성공 6/7 셋 다 같음 · 금지 실행 0 · B→D 의 모든 차는 구간이 0 을 걸침(**모른다**) · D→F 는 입력 −227(구간 −295~−136) 인데 출력 +171 · 지연 +1.5 s · 비용 +$0.0008 | **사전등록 밖이다**(CLI 하네스가 붙는다, 반복 1). 방향은 선행조사(arXiv:2609.32961)가 말한 "토큰이 줄어도 느려질 수 있다" 와 같다. **사소한 설명을 못 죽였다**: 적응 프롬프트의 concise 지시가 원래 지시의 "rationale 한 줄" 을 빠뜨렸다(prompt-text-1) — 아래 재측정에서 고친 뒤 다시 쟀다 |
| `claude-cli` 재측정 B · D · F, 반복 2 (concise 에 "rationale 한 줄" 을 되돌린 뒤) — [`eval/results/claude-cli_재측정_2026-10-01.md`](eval/results/claude-cli_재측정_2026-10-01.md) · 읽기는 [`eval/PREREG_간결지시_재측정.md`](eval/PREREG_간결지시_재측정.md) | D→F 출력 +14(구간 0 걸침, 앞은 +171) · F 의 rationale 68.6자 ≈ D 73.0자 · D→F 입력 +270(구간 0 걸침) · 비용 "늘었다" | 앞의 출력 증가는 **지시문 결함 때문**이었다(돌리기 전에 적은 읽기대로). 입력 감소가 사라진 것은 반복 1 에서 concise 아래 못 읽는 답(A0) 한 번 -> `answer_reliability=LOW` -> 품질 우선으로 바뀌어 프롬프트가 길어졌기 때문이다(규칙대로, 다만 세션 초반에 민감하다). **비용 차는 캐시 상태의 차다** — 앞 측정이 쓴 캐시를 B · D 가 읽었고 F 는 지시문이 바뀌어 못 읽었다 |
| `claude-cli` 재측정 2: 보호 장치 완화(usage-model-2) + 순서 섞음, B · D · F, 반복 2 — [`eval/results/claude-cli_재측정2_2026-10-01.md`](eval/results/claude-cli_재측정2_2026-10-01.md) · 읽기 [`eval/PREREG_보호장치_재측정.md`](eval/PREREG_보호장치_재측정.md) | F 의 두 세션 모두 A0 한 번 -> 품질 우선으로 **안 바뀜** · D→F 전체 입력 모른다 / 캐시 안 된 입력 +822(늘었다) · 비용 +$0.0036/과업 | **완화가 들었다.** 두 입력 지표가 갈려 입력은 판정 안 함(사전등록 고침 1-2). F 가 비싼 까닭은 **상태에 따라 지시문이 바뀌어 provider 캐시가 깨지기 때문**(미리 적어 둔 구조적 치우침) — 줄인 토큰보다 캐시 손실이 클 수 있다. 새 가설 후보: concise 지시에서 A0 3/37 대 full 0/93(단측 Fisher 0.022, 사후 · 비독립이라 발견 아님) |
| `claude-cli` 재측정 3: 캐시를 지키는 배치(prompt-text-3) + `--fresh`, B · D · F, 반복 2 — [`eval/results/claude-cli_재측정3_2026-10-02.md`](eval/results/claude-cli_재측정3_2026-10-02.md) · 읽기 [`eval/PREREG_캐시배치_재측정.md`](eval/PREREG_캐시배치_재측정.md) | 캐시 읽기가 **모든 실행에서 0** · D→F 비용 +$0.00096(구간 0 걸침) · F 의 A0 0 · B→D 지연 +2.1 s(늘었다) | 탐침으로 가렸다: **claude -p 는 캐시 지점을 프롬프트 끝에만 둔다** — 시스템 글이 같아도 사용자 글이 다르면 읽기 0. 그래서 배치의 캐시 효과는 claude-cli 로 못 보고, 앞 측정들의 캐시 읽기는 **같은 과업을 되풀이한 인공물**이었다. 되풀이를 없애자 F 의 비용 불리가 사라졌다. Claude API 어댑터에 시스템 글 끝 캐시 지점을 넣었다(못 재봄). 네 측정 내내 같은 방향: **적응 맥락이 꺼냄을 늘리고(6 대 2~4) 느리다** — 후보 |
| 과업 t6 | 세 칸 모두 실패 | 다 식힌 세계에서 Claude 가 팬 고장 srv05 에 티켓을 열었다. 과업은 "아무것도 하지 마라" 를 기대했다 — **과업 정의의 결함**이지 정책의 효과가 아니다 |
| 비용 대조 | 일치 | 한 실행: 입력 2,140(거의 다 1 시간 캐시 쓰기, $4/MTok) + 출력 94($10/MTok) = $0.0095 ≈ CLI 보고 $0.009496 |

## 돌리기

action 은 필수 의존이다. `pip install -e .` 가 고정한 sha 를 깐다(또는 `PYTHONPATH` 에 cogito5170/action).

```bash
python3 -m ms demo                                   # 처음의 데이터센터 예시(대본 LLM)
python3 -m ms ask ms/examples/datacenter.json --telemetry ms/examples/datacenter_telemetry.jsonl \
    --task "과열된 서버를 처리하라" --provider claude-cli --context adaptive --prompt adaptive   # 진짜 Claude(로그인으로)
python3 -m ms ask ... --provider claude --model claude-opus-5-5 --stream     # ANTHROPIC_API_KEY
python3 -m ms ask ... --provider openai --model <모형>                        # OPENAI_API_KEY (모형 기본값을 지어내지 않는다)
python3 -m ms ask ... --provider sim-gemini                                   # 모의 -- 배선 확인
python3 -m unittest tests.test_ms tests.test_runtime tests.test_sensing      # 시험 수는 discover 로 확인
```

```python
from ms import StateManager, ToolRegistry
from ms.providers import make_provider
from ms.runtime import Runtime
from ms.policy import AdaptiveContext, AdaptivePrompt

rt = Runtime(world, registry, {"claude": make_provider("claude", "claude-opus-5-5"),
                               "openai": make_provider("openai", "<모형>")},
             context_selector=AdaptiveContext(), prompt_selector=AdaptivePrompt(), grants={"open_ticket"})
rt.open_session("s1", {"token_budget": 20000, "context_budget": 4000, "latency_budget_ms": 8000})
out = rt.handle({"session": "s1", "task": "...", "queries": [...], "provider": "claude"})
rt.feedback(out["run_id"], user_correction=False)     # 사람의 고침도 텔레메트리다
```

## 알고 쓸 것

- **가설에 아직 답이 없다.** 사전등록대로 A~F 를 진짜 API 둘로, 반복 3 으로 돌려야 한다. 이 컨테이너에서는 OpenAI 를 못 부른다.
- **provider 캐시가 칸 비교를 오염시킨다**(재측정에서 실제로 났다). 앞 칸이 쓴 캐시를 뒤 칸이 읽으면 비용이 4 배까지 갈린다. 진짜 측정은
  칸 순서를 섞거나 캐시 안 된 입력(`input_tokens − cached_input_tokens`)으로도 비교해야 한다.
- 품질 상태(answer_reliability LOW · correction_rate HIGH)는 **표본 3 개 이상 · 사건 2 번 이상**일 때만 정해진다(usage-model-2).
  usage-model-1 은 실패 한 번으로 LOW 가 됐다.
- **프롬프트 배치는 기본이 stable_prefix(prompt-text-3)다.** 시스템 글은 모든 계획 · 상태에서 바이트가 같고, 계획이 바꾸는 것은
  사용자 글(STATE 뒤)로 간다. 그 대가로 concise 지시는 적용하지 않는다(`unsupported` 에 적힌다). 예전 배치는 `--layout legacy`.
- 캐시를 실제로 쓰려면 provider 가 **시스템 글 끝**에서 캐시해야 한다. Claude API 어댑터는 그 자리에 `cache_control` 을 둔다.
  claude-cli 는 지점을 고를 수 없어 **완전히 같은 프롬프트만** 캐시에서 읽는다(실측). 평가에서 같은 과업을 되풀이하면 그것이
  측정을 오염시키므로 `--fresh` 를 쓴다.
- 적응 맥락은 지금까지 **꺼냄을 늘리고 느렸다**(네 측정 같은 방향, 독립 아님). 맥락을 줄이는 정책이 판 수를 늘리는 값을 같이 봐야 한다.
- `context_tokens` · `retrieved_tokens` 는 **추정**이다(입력 토큰 × 글자 비율). claude-cli 처럼 provider 가 우리 프롬프트 밖의 토큰
  (하네스 ~1,100)을 더하면 맥락 몫을 **부풀린다.** 그 상태(context_pressure)는 그만큼 과하게 HIGH 가 된다.
- 상태의 문턱 · 정책의 규칙은 손으로 둔 것이다. 바꾸면 판본을 올린다 — 재현이 판본을 본다.
- Gemini 의 JSON 스키마 강제 필드는 확인하지 못해 쓰지 않는다. Gemini 2.5 의 `thinkingBudget` 은 수준을 토큰 수로 바꿔야 해서 쓰지 않는다.
- OpenAI 의 `reasoning.effort` 는 추론 모형에서만 받는다 — 어댑터는 모형 목록을 추측하지 않고 그대로 보낸다(거절되면 그 실행이 오류).
- 한 판에 제안 하나. 그래프 · 측정 창은 메모리에만. 스레드 안전하지 않다.
- 선행조사: [`paper/선행조사/MS.md`](paper/선행조사/MS.md) (모형 · 그래프 · 중재자) · [`paper/선행조사/정책런타임.md`](paper/선행조사/정책런타임.md)
  (가장 가까운 것: *Beyond Token Savings*, arXiv:2609.32961 — 같은 물음. **전문을 안 읽었다**).
