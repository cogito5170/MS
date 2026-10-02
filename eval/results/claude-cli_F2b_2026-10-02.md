> **이 결과는 가설의 증거가 아니다.** claude-cli 는 Claude Code 하네스가 붙어 API 의 Claude 와 같지 않다(사전등록: B · D · F 가 아니라 따로)

# 평가 -- eval/tasks/datacenter_tasks3.json · 반복 5 · 순서 interleaved(씨앗 0) · 배치 stable_prefix · fresh True · 2026-10-02T10:05:30

판본: {'usage_model': 'usage-model-4', 'tasks': 'datacenter-tasks-3', 'prompt_text': 'prompt-text-4'}

## provider 능력(같다고 가정하지 않는다)

| 자리 | 무엇 | 능력 |
|---|---|---|
| claude | claude-cli | api: claude -p (Claude Code harness) · output_schema: prompt only · reasoning: --effort low/medium/high; 'off' unsupported · cached_tokens: reported (Claude usage shape) · ttft: not available (no stream) · stream: False · native_tools: False · cost: reported (total_cost_usd) · prefix_cache: whole-prompt only -- the CLI places the breakpoint; same system + different user text reads 0 (measured 2026-10-02) · not_equivalent_to_api: Claude Code harness adds system/context tokens |

## 칸별

| 칸 | 성공 | 고침 | 금지 실행 | 재시도 | 입력 | 캐시 안 된 입력 | 출력 | 총 토큰 | 지연 중앙(ms) | 비용 | 도구 | 꺼냄 | DENY 율 | 회복 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | 0.88 | 0 | 0 | 0.00 | 3531 | 3531 | 120 | 3652 | 4257 | 1.2261 | 50 | 0 | 0.00 | - |
| G | 0.88 | 0 | 0 | 0.00 | 2626 | 2626 | 131 | 2757 | 4285 | 0.9451 | 50 | 0 | 0.00 | - |
| H | 0.88 | 0 | 0 | 0.00 | 2626 | 2626 | 140 | 2766 | 4430 | 0.9522 | 50 | 0 | 0.00 | - |

## 동작점 · 상태 읽기 (사소한 설명 S4 · S5 · S6)

상태 읽기: {'kind': 'dc', 'path': '/home/user/cogito5170/dc', 'commit': '1387318', 'purpose': 'context_runtime'}

| 칸 | 실행 | 압력 HIGH 계획 | DC 상태 ≠ snapshot (실행 · 상태) | 모형 |
|---|---|---|---|---|
| B | 80 | 0 | 80 ['tool_churn'] | claude-sonnet-5-5 |
| G | 80 | 80 | 80 ['tool_churn'] | claude-sonnet-5-5 |
| H | 80 | 80 | 80 ['tool_churn'] | claude-sonnet-5-5 |

## F2b 층별 판정 (사전등록 eval/PREREG_F2b_없음확인.md · 워밍업 45 실행은 뺌)

| 판정 | 수 | 차 | 95% 구간 | 읽기 |
|---|---|---|---|---|
| R1a | 없음 층 꺼냄 G−B | 0.000 | [0.0, 0.0] | **모른다** |
| R1b | 꺼냄 (없음 − 있음) 상호작용 | 0.000 | [0.0, 0.0] | **모른다** |
| R2 | 있음 층 입력 G−B | -907.125 | [-953.5, -866.0] | **확인** |
| R4 | 없음 층 꺼냄 H−G | 0.000 | [0.0, 0.0] | **모른다** |
| R3 | 있음 층 G 꺼냄 비율 p̂ · 손익분기 p* | p̂ 0.000 · p* - | [0.0, 0.0] | 절약 -907 · 꺼냄 하나 - |
| quality_absent_B->G | | 0.000 | [0.0, 0.0] | 비열등 |
| quality_absent_G->H | | 0.000 | [0.0, 0.0] | 비열등 |
| quality_present_B->G | | 0.000 | [0.0, 0.0] | 비열등 |
| quality_present_G->H | | 0.000 | [0.0, 0.0] | 비열등 |
| R6_absent | | -903.750 | [-945.5, -870.375] | 기술 |
| R6_present | | -907.250 | [-948.5, -866.0] | 기술 |

- S1 R1a 과업별: P1-absent 0.00 · P2-absent 0.00 · P3-absent 0.00 · P4-absent 0.00 · P5-absent 0.00 · P6-absent 0.00 · P7-absent 0.00 · P8-absent 0.00 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 R2 과업별: P1-present -866.00 · P2-present -866.00 · P3-present -866.00 · P4-present -866.00 · P5-present -1041.00 · P6-present -866.00 · P7-present -1020.00 · P8-present -866.00 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 R4 과업별: P1-absent 0.00 · P2-absent 0.00 · P3-absent 0.00 · P4-absent 0.00 · P5-absent 0.00 · P6-absent 0.00 · P7-absent 0.00 · P8-absent 0.00 · 하나 빼면 부호가 바뀌는 과업: 없음
- S4 G: 압력 HIGH 80/80 · 품질 상태가 정해진 실행 80/80 · S7 꺼낸 handle {}
- S4 H: 압력 HIGH 80/80 · 품질 상태가 정해진 실행 80/80 · S7 꺼낸 handle {}

## 짝 비교(사전등록 판정)

### B->G -- 덜 자르는 적응 맥락 대 고정(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 80/80 · 압력 HIGH 계획으로 돈 실행 80/80 (S4 동작점)
- S1 retrievals: 과업별 차 P1-absent 0.0 · P1-present 0.0 · P2-absent 0.0 · P2-present 0.0 · P3-absent 0.0 · P3-present 0.0 · P4-absent 0.0 · P4-present 0.0 · P5-absent 0.0 · P5-present 0.0 · P6-absent 0.0 · P6-present 0.0 · P7-absent 0.0 · P7-present 0.0 · P8-absent 0.0 · P8-present 0.0 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 total_ms: 과업별 차 P1-absent 464.5 · P1-present -52.2 · P2-absent 206.3 · P2-present -99.3 · P3-absent -2.8 · P3-present -109.8 · P4-absent -73.5 · P4-present -162.6 · P5-absent 614.7 · P5-present -123.5 · P6-absent 70.9 · P6-present -41.5 · P7-absent 29.7 · P7-present 161.9 · P8-absent 142.6 · P8-present 613.8 · 하나 빼면 부호가 바뀌는 과업: 없음
- input_tokens: 고정 3531.4 -> 적응 2626.1 · 차 -905.4 구간 [-939.5, -876.9] -> 줄었다 (과업 16)
- uncached_input_tokens: 고정 3531.4 -> 적응 2626.1 · 차 -905.4 구간 [-939.5, -876.9] -> 줄었다 (과업 16)
- output_tokens: 고정 120.5 -> 적응 131.4 · 차 10.9 구간 [-1.2, 25.2] -> 모른다 (과업 16)
- total_tokens: 고정 3651.9 -> 적응 2757.5 · 차 -894.5 구간 [-932.4, -861.6] -> 줄었다 (과업 16)
- total_ms: 고정 4364.6 -> 적응 4467.1 · 차 102.4 구간 [-6.6, 223.3] -> 모른다 (과업 16)
- retries: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- cost_usd: 고정 0.01533 -> 적응 0.01181 · 차 -0.00351 구간 [-0.00371, -0.00332] -> 줄었다 (과업 16)
- rationale_chars: 고정 76.8 -> 적응 80.8 · 차 4.0 구간 [-2.3, 10.6] -> 모른다 (과업 16)
- retrievals: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- llm_calls: 고정 1.0 -> 적응 1.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)

### G->H -- 덮음 선언(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 80/80 · 압력 HIGH 계획으로 돈 실행 80/80 (S4 동작점)
- S1 retrievals: 과업별 차 P1-absent 0.0 · P1-present 0.0 · P2-absent 0.0 · P2-present 0.0 · P3-absent 0.0 · P3-present 0.0 · P4-absent 0.0 · P4-present 0.0 · P5-absent 0.0 · P5-present 0.0 · P6-absent 0.0 · P6-present 0.0 · P7-absent 0.0 · P7-present 0.0 · P8-absent 0.0 · P8-present 0.0 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 total_ms: 과업별 차 P1-absent -241.2 · P1-present 66.7 · P2-absent 368.3 · P2-present -335.0 · P3-absent 101.7 · P3-present 150.0 · P4-absent 300.3 · P4-present 170.6 · P5-absent -125.5 · P5-present 413.1 · P6-absent 205.8 · P6-present 325.2 · P7-absent 165.5 · P7-present -101.8 · P8-absent 233.8 · P8-present 134.8 · 하나 빼면 부호가 바뀌는 과업: 없음
- input_tokens: 고정 2626.1 -> 적응 2625.9 · 차 -0.1 구간 [-2.8, 2.4] -> 모른다 (과업 16)
- uncached_input_tokens: 고정 2626.1 -> 적응 2625.9 · 차 -0.1 구간 [-2.8, 2.4] -> 모른다 (과업 16)
- output_tokens: 고정 131.4 -> 적응 140.3 · 차 8.9 구간 [1.5, 17.4] -> 늘었다 (과업 16)
- total_tokens: 고정 2757.5 -> 적응 2766.2 · 차 8.8 구간 [0.7, 18.3] -> 늘었다 (과업 16)
- total_ms: 고정 4467.1 -> 적응 4581.6 · 차 114.5 구간 [4.1, 206.7] -> 늘었다 (과업 16)
- retries: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- cost_usd: 고정 0.01181 -> 적응 0.01190 · 차 0.00009 구간 [1e-05, 0.00018] -> 늘었다 (과업 16)
- rationale_chars: 고정 80.8 -> 적응 82.1 · 차 1.2 구간 [-2.3, 4.8] -> 모른다 (과업 16)
- retrievals: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- llm_calls: 고정 1.0 -> 적응 1.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)

### B->H -- 덮음 선언 대 고정(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 80/80 · 압력 HIGH 계획으로 돈 실행 80/80 (S4 동작점)
- S1 retrievals: 과업별 차 P1-absent 0.0 · P1-present 0.0 · P2-absent 0.0 · P2-present 0.0 · P3-absent 0.0 · P3-present 0.0 · P4-absent 0.0 · P4-present 0.0 · P5-absent 0.0 · P5-present 0.0 · P6-absent 0.0 · P6-present 0.0 · P7-absent 0.0 · P7-present 0.0 · P8-absent 0.0 · P8-present 0.0 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 total_ms: 과업별 차 P1-absent 223.4 · P1-present 14.5 · P2-absent 574.6 · P2-present -434.3 · P3-absent 98.8 · P3-present 40.2 · P4-absent 226.8 · P4-present 8.0 · P5-absent 489.2 · P5-present 289.6 · P6-absent 276.7 · P6-present 283.7 · P7-absent 195.2 · P7-present 60.1 · P8-absent 376.3 · P8-present 748.6 · 하나 빼면 부호가 바뀌는 과업: 없음
- input_tokens: 고정 3531.4 -> 적응 2625.9 · 차 -905.5 구간 [-940.4, -876.3] -> 줄었다 (과업 16)
- uncached_input_tokens: 고정 3531.4 -> 적응 2625.9 · 차 -905.5 구간 [-940.4, -876.3] -> 줄었다 (과업 16)
- output_tokens: 고정 120.5 -> 적응 140.3 · 차 19.8 구간 [5.2, 36.8] -> 늘었다 (과업 16)
- total_tokens: 고정 3651.9 -> 적응 2766.2 · 차 -885.7 구간 [-922.5, -853.1] -> 줄었다 (과업 16)
- total_ms: 고정 4364.6 -> 적응 4581.6 · 차 217.0 구간 [86.1, 344.1] -> 늘었다 (과업 16)
- retries: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- cost_usd: 고정 0.01533 -> 적응 0.01190 · 차 -0.00342 구간 [-0.00362, -0.00322] -> 줄었다 (과업 16)
- rationale_chars: 고정 76.8 -> 적응 82.1 · 차 5.3 구간 [-0.2, 11.3] -> 모른다 (과업 16)
- retrievals: 고정 0.0 -> 적응 0.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)
- llm_calls: 고정 1.0 -> 적응 1.0 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 16)

