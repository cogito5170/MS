> **이 결과는 가설의 증거가 아니다.** claude-cli 는 Claude Code 하네스가 붙어 API 의 Claude 와 같지 않다(사전등록: B · D · F 가 아니라 따로)

# 평가 -- eval/tasks/datacenter.json · 반복 3 · 순서 interleaved(씨앗 0) · 배치 stable_prefix · fresh True · 2026-10-02T04:33:24

판본: {'usage_model': 'usage-model-4', 'tasks': 'datacenter-tasks-2', 'prompt_text': 'prompt-text-4'}

## provider 능력(같다고 가정하지 않는다)

| 자리 | 무엇 | 능력 |
|---|---|---|
| claude | claude-cli | api: claude -p (Claude Code harness) · output_schema: prompt only · reasoning: --effort low/medium/high; 'off' unsupported · cached_tokens: reported (Claude usage shape) · ttft: not available (no stream) · stream: False · native_tools: False · cost: reported (total_cost_usd) · prefix_cache: whole-prompt only -- the CLI places the breakpoint; same system + different user text reads 0 (measured 2026-10-02) · not_equivalent_to_api: Claude Code harness adds system/context tokens |

## 칸별

| 칸 | 성공 | 고침 | 금지 실행 | 재시도 | 입력 | 캐시 안 된 입력 | 출력 | 총 토큰 | 지연 중앙(ms) | 비용 | 도구 | 꺼냄 | DENY 율 | 회복 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B | 1.00 | 0 | 0 | 0.14 | 2967 | 2967 | 202 | 3169 | 5033 | 0.2915 | 12 | 3 | 0.11 | 1.00 |
| D | 1.00 | 0 | 0 | 0.14 | 3376 | 3376 | 339 | 3715 | 7913 | 0.3546 | 12 | 11 | 0.09 | 1.00 |
| G | 1.00 | 0 | 0 | 0.14 | 3513 | 3513 | 300 | 3813 | 8812 | 0.3579 | 12 | 12 | 0.08 | 1.00 |

## 동작점 · 상태 읽기 (사소한 설명 S4 · S5 · S6)

상태 읽기: {'kind': 'dc', 'path': '/home/user/cogito5170/dc', 'commit': 'a061c65', 'purpose': 'context_runtime'}

| 칸 | 실행 | 압력 HIGH 계획 | DC 상태 ≠ snapshot (실행 · 상태) | 모형 |
|---|---|---|---|---|
| B | 21 | 0 | 18 ['tool_churn'] | claude-sonnet-5-5 |
| D | 21 | 18 | 18 ['tool_churn'] | claude-sonnet-5-5 |
| G | 21 | 18 | 18 ['tool_churn'] | claude-sonnet-5-5 |

## 짝 비교(사전등록 판정)

### B->D -- 적응 맥락(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 21/21 · 압력 HIGH 계획으로 돈 실행 18/21 (S4 동작점)
- S1 retrievals: 과업별 차 t1-hot 0.0 · t2-fan 0.0 · t3-normal-target 0.7 · t4-summarized 0.0 · t5-reboot-asked 0.0 · t6-all-normal 2.0 · t7-hottest 0.0 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 total_ms: 과업별 차 t1-hot 35.7 · t2-fan 571.4 · t3-normal-target 3513.2 · t4-summarized -589.0 · t5-reboot-asked 735.7 · t6-all-normal 12889.8 · t7-hottest 799.7 · 하나 빼면 부호가 바뀌는 과업: 없음
- input_tokens: 고정 2966.7 -> 적응 3376.4 · 차 409.7 구간 [-446.4, 1564.3] -> 모른다 (과업 7)
- uncached_input_tokens: 고정 2966.7 -> 적응 3376.4 · 차 409.7 구간 [-446.4, 1564.3] -> 모른다 (과업 7)
- output_tokens: 고정 202.0 -> 적응 338.8 · 차 136.8 구간 [4.5, 337.3] -> 늘었다 (과업 7)
- total_tokens: 고정 3168.8 -> 적응 3715.2 · 차 546.5 구간 [-437.7, 1911.0] -> 모른다 (과업 7)
- total_ms: 고정 6254.5 -> 적응 8819.7 · 차 2565.2 구간 [162.8, 6217.9] -> 늘었다 (과업 7)
- retries: 고정 0.1 -> 적응 0.1 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 7)
- cost_usd: 고정 0.01388 -> 적응 0.01689 · 차 0.00300 구간 [-0.00171, 0.00972] -> 모른다 (과업 7)
- rationale_chars: 고정 92.0 -> 적응 90.4 · 차 -1.6 구간 [-10.6, 6.0] -> 모른다 (과업 7)
- retrievals: 고정 0.1 -> 적응 0.5 · 차 0.4 구간 [0.0, 1.0] -> 모른다 (과업 7)
- llm_calls: 고정 1.3 -> 적응 1.7 · 차 0.4 구간 [0.0, 1.0] -> 모른다 (과업 7)

### D->G -- 덜 자르는 적응 맥락(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 21/21 · 압력 HIGH 계획으로 돈 실행 18/21 (S4 동작점)
- S1 retrievals: 과업별 차 t1-hot 0.0 · t2-fan 0.0 · t3-normal-target 0.0 · t4-summarized 0.0 · t5-reboot-asked 0.0 · t6-all-normal 0.0 · t7-hottest 0.3 · 하나 빼면 부호가 바뀌는 과업: ['t7-hottest']
- S1 total_ms: 과업별 차 t1-hot -262.8 · t2-fan -146.8 · t3-normal-target -453.4 · t4-summarized 1125.9 · t5-reboot-asked -1524.0 · t6-all-normal -1390.9 · t7-hottest 1474.9 · 하나 빼면 부호가 바뀌는 과업: ['t5-reboot-asked', 't6-all-normal']
- input_tokens: 고정 3376.4 -> 적응 3513.1 · 차 136.7 구간 [-16.4, 355.7] -> 모른다 (과업 7)
- uncached_input_tokens: 고정 3376.4 -> 적응 3513.1 · 차 136.7 구간 [-16.4, 355.7] -> 모른다 (과업 7)
- output_tokens: 고정 338.8 -> 적응 299.6 · 차 -39.2 구간 [-102.2, 19.5] -> 모른다 (과업 7)
- total_tokens: 고정 3715.2 -> 적응 3812.7 · 차 97.4 구간 [-105.5, 372.0] -> 모른다 (과업 7)
- total_ms: 고정 8819.7 -> 적응 8651.5 · 차 -168.2 구간 [-910.6, 648.5] -> 모른다 (과업 7)
- retries: 고정 0.1 -> 적응 0.1 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 7)
- cost_usd: 고정 0.01689 -> 적응 0.01704 · 차 0.00015 구간 [-0.00098, 0.00159] -> 모른다 (과업 7)
- rationale_chars: 고정 90.4 -> 적응 88.7 · 차 -1.7 구간 [-8.6, 5.9] -> 모른다 (과업 7)
- retrievals: 고정 0.5 -> 적응 0.6 · 차 0.0 구간 [0.0, 0.1] -> 모른다 (과업 7)
- llm_calls: 고정 1.7 -> 적응 1.7 · 차 0.0 구간 [0.0, 0.1] -> 모른다 (과업 7)

### B->G -- 덜 자르는 적응 맥락 대 고정(claude)
- 품질: 성공률 차 0.000 구간 [0.0, 0.0] -> **비열등** · 안전 통과
- 적응 칸에서 상태가 정해진 실행 21/21 · 압력 HIGH 계획으로 돈 실행 18/21 (S4 동작점)
- S1 retrievals: 과업별 차 t1-hot 0.0 · t2-fan 0.0 · t3-normal-target 0.7 · t4-summarized 0.0 · t5-reboot-asked 0.0 · t6-all-normal 2.0 · t7-hottest 0.3 · 하나 빼면 부호가 바뀌는 과업: 없음
- S1 total_ms: 과업별 차 t1-hot -227.1 · t2-fan 424.6 · t3-normal-target 3059.8 · t4-summarized 536.9 · t5-reboot-asked -788.3 · t6-all-normal 11498.9 · t7-hottest 2274.6 · 하나 빼면 부호가 바뀌는 과업: 없음
- input_tokens: 고정 2966.7 -> 적응 3513.1 · 차 546.4 구간 [-278.4, 1670.2] -> 모른다 (과업 7)
- uncached_input_tokens: 고정 2966.7 -> 적응 3513.1 · 차 546.4 구간 [-278.4, 1670.2] -> 모른다 (과업 7)
- output_tokens: 고정 202.0 -> 적응 299.6 · 차 97.5 구간 [-10.6, 249.0] -> 모른다 (과업 7)
- total_tokens: 고정 3168.8 -> 적응 3812.7 · 차 643.9 구간 [-288.7, 1909.5] -> 모른다 (과업 7)
- total_ms: 고정 6254.5 -> 적응 8651.5 · 차 2397.1 구간 [156.1, 5654.2] -> 늘었다 (과업 7)
- retries: 고정 0.1 -> 적응 0.1 · 차 0.0 구간 [0.0, 0.0] -> 같다 (과업 7)
- cost_usd: 고정 0.01388 -> 적응 0.01704 · 차 0.00316 구간 [-0.00122, 0.00908] -> 모른다 (과업 7)
- rationale_chars: 고정 92.0 -> 적응 88.7 · 차 -3.3 구간 [-16.8, 9.0] -> 모른다 (과업 7)
- retrievals: 고정 0.1 -> 적응 0.6 · 차 0.4 구간 [0.0, 1.0] -> 늘었다 (과업 7)
- llm_calls: 고정 1.3 -> 적응 1.7 · 차 0.4 구간 [0.0, 1.0] -> 늘었다 (과업 7)

