# 선행조사 -- F2b: 맥락을 줄이면 꺼냄이 느는가, '없음' 을 확인하는 과업에서 특히 그런가 (2026-10-02)

코드(과업 묶음 `datacenter-tasks-3` · 층화 하니스)보다 먼저 커밋한다. 사전등록은 `eval/PREREG_F2b_없음확인.md`.

**이 조사는 얕다.** 이 환경에서는 arxiv.org 가 네트워크 정책으로 막혀 있다. 그래서 아래는 **전부 검색 조각만 보고** 적었다(`[출처:조각]`).
전문 · 초록을 읽은 것은 없다. 논문이 무엇을 **안 했는지**는 조각으로 알 수 없다 -- 그래서 "우리만 했다" 는 주장은 하지 않는다.

## 1. 가장 가까운 선행연구

| 우리 것 | 가장 가까운 것 | 확인수준 | 겹치는 것 · 다른 것 |
|---|---|---|---|
| **F2 의 현상**: 맥락을 줄이면 완료율은 그대로인데 꺼냄(검색) 호출이 는다 | **Liu**, *What Does Context Compression Cost an Agent? Interaction Costs Unrevealed by Task-Completion Metrics* (arXiv:2608.16370, 2026) | [출처:조각] | **거의 같은 현상이 이미 보고됐다.** 결정론적 계획 환경, 24 턴 예산, 압축 비율과 연산자(버리기 · 사실 보존)를 바꿨다. 여섯 모형 · 체제 비교 **모두에서 검색 호출이 늘었고**, 늘어난 상호작용의 거의 전부가 검색이었다. 실행 호출은 그대로였다. GPT-5.5 는 완료 80 → 85%(p=1.0)인데 검색이 약 세 배(+42.9 호출)였다. **같은 압축 비율의 사실 보존 연산자는 완료를 지키면서 추가 검색 대부분을 피했다.** **ALFWorld 에서는 검색 급증이 없었다 -- 환경에 달렸다.** 우리 F2 는 이것의 작은 재현이다. 새 발견이라고 말하지 않는다 |
| **H1**: 그 꺼냄은 '없음' 을 확인해야 하는 과업에 몰린다 | **Min 외**, *When Absence Is Evidence: Evaluating Completeness-Sensitive Negative Reasoning in LLMs* (arXiv:2608.04591, 2026), CROWN-QA | [출처:조각] | '안 보임' 은 근거가 **질의 범위를 다 덮을 때만** "없다" 를 허락한다(Certified-Negative 대 Unknown). 세 모형 계열 모두 **과잉 닫음**이 컸다: 덮지 않는 근거로 "없다" 라고 답했다. 판단만 보고 도구 · 검색은 조각에 없다. 우리 쪽은 반대 방향으로 보인다 -- 요약이 "status normal 7/7" 을 말해도 모형은 handle 을 꺼냈다. **꺼낼 도구가 있으면 범위를 덮으려고 꺼낸다** 는 것이 H1 의 기제 후보이고, 이 논문은 그 문제(범위 덮음)를 정의한다. 기제를 이 논문에서 빌린다고 적는다 |
| 없음을 놓침 | *LLM Judges Verify Presence, Not Absence: Omission Blindness in AI Clinical Notes* (arXiv:2608.31016) · AbsenceBench | [출처:조각] | 삽입은 86~99% F1 로 잡는데 빠뜨림은 평균 57% 떨어진다. 판정자 쪽 문제다. 우리 과업과는 비대칭(있음 · 없음)만 겹친다 |
| **R2 · R3**: 토큰 감소가 꺼냄 비용을 넘는가 · 손익분기 | *Token Reduction Is Not Cost Reduction: An Empirical Study of End-to-End Efficiency in API-Based Coding Agents* (arXiv:2607.12161) | [출처:조각] | **Claude Code** 에서 도구 출력 압축을 쟀다(청구된 비용 기준). 가장 센 압축은 도구 출력 토큰을 38.4% 줄였는데 **비용은 6.8% 늘었다**. 토큰 감소와 비용 감소의 상관은 r = 0.15 였다. 까닭으로 둘을 든다: 입력 비용의 대부분이 **프롬프트 캐시 흐름**이고, 압축이 **추가 검색 · 진단 · 턴**을 부른다. 그래서 '성공 보정 끝-끝 비용' 으로 재라고 한다. 우리 R3(p 대 p*)는 같은 생각이다. 다른 것: 우리 claude-cli 측정은 `--fresh` 라 캐시 읽기가 0 이다 -- **캐시를 뺀 채** 재는 셈이고, 진짜 쓰임의 비용 결론으로 옮기면 안 된다 |
| 압축 결정을 체계적으로 바꿔 보기 | *Beyond Token Savings: A Systematic Study of Context Compression in LLM Agents* (arXiv:2609.32961) | [출처:조각] | 공개 가중치 모형 셋 · SWE-bench Verified · Terminal-Bench 1.0 · 약 35,000 실행. 성공 · 토큰 · 끝-끝 지연 · 비용을 함께 쟀다. 무엇을 찾았는지는 조각에 없다 -- **가장 먼저 읽어야 할 것**이다 |
| 결정을 지키는 압축 | *FOCUS: Training-Free Decision-Preserving Context Compression for LLM Agents* (arXiv:2609.37590) | [출처:조각] | 미래 결정 궤적에 인과적으로 필요한 구간만 남긴다(몬테카를로 롤아웃). 최대 맥락 −48%, 성공 +8.9 %p. 우리 CR 은 결정론적 규칙(예산 · 요약 · handle)이라 다르다. 다만 "꺼냄을 부르지 않는 줄임" 의 방향을 준다 |
| 꺼냄 handle(필요할 때 되찾기) | *TokenPilot: Cache-Efficient Context Management for LLM Agents* (arXiv:2606.17016) | [출처:조각] | 필요할 때 원본을 되찾는 복구 장치가 있다 -- 우리 RETRIEVE handle 과 같은 자리다 |
| 압축 정책을 배우기 | ACON (arXiv:2510.00615) · *Active Context Compression* (arXiv:2601.07190) · *Context as an Environment* (arXiv:2608.21690) · TRACER(도구별 보존, arXiv:2608.29363) | [출처:조각] | 압축 지침 최적화 · 모형이 스스로 압축 · 맥락을 환경으로 · 도구별 보존을 강화학습으로. 우리 범위(결정론적 CR 의 한 변수)보다 넓다 |
| 언제 꺼낼까 | Self-RAG (ICLR 2024) · Adaptive-RAG (arXiv:2403.14403) · SeaKR (arXiv:2406.19215) · FLARE | [출처:조각] | 매개 지식 대 외부 검색의 '언제' 문제다. 우리는 **같은 정보를 우리가 숨겼다가** 되찾는 문제라 다르다 |

## 2. 그래서 F2b 에서 새로운 것은 좁다

- **현상 자체(줄이면 꺼냄이 는다)는 새롭지 않다.** Liu (2608.16370)가 여섯 비교에서 보였다. 그것이 **환경에 달렸다**는 것도 보였다(ALFWorld 에서는 없음).
- 우리가 더할 수 있는 것은 **'어떤 과업에서' 의 한 후보 설명**이다. 같은 과업 글을 있음 · 없음 세계에 짝지어(바이트까지 같은 글, 목표 조건 하나만 다른 세계), '없음을 확인해야 하는가' 만 바꾼다. 조각으로는 이렇게 짝지은 측정을 못 봤다. 그러나 **안 했다고는 말 못 한다**(전문을 안 읽었다).
- 기제의 이름은 CROWN-QA 에서 빌린다(범위를 덮는 근거). 우리 관찰은 판단이 아니라 **도구가 있을 때의 행동**이다.
- R3(손익분기)는 *Token Reduction Is Not Cost Reduction* 의 '끝-끝 비용' 과 같은 생각이다. 새것이 아니다.

## 3. 설계에 주는 것

1. **결과를 쓸 때 Liu (2608.16370)를 앞에 둔다.** F2 · F2b 의 '꺼냄 증가' 는 재현이다. 기여는 짝지은 층화와 그 결과뿐이다.
2. **요약이 '범위를 덮는다' 고 말하게 하면 꺼냄이 줄까?** 사실 보존 연산자가 추가 검색 대부분을 피했다는 것(Liu)과 '범위 덮음'(CROWN-QA)을 합치면 나오는 물음이다. 지금 요약은 `count 8 · status normal 7/7` 이다. 여기에 "이 요약은 fleet 질의의 결과 8 행 전부를 덮는다" 같은 **덮음 선언**을 더한 칸을 생각할 수 있다. **F2b 의 사전등록에는 넣지 않았다**(제안만) -- 넣으면 칸 · 확률 · 비용이 바뀐다. baseline 판단으로 남긴다.
3. 비용 결론은 캐시를 뺀 claude-cli 값이다. 진짜 쓰임의 비용으로 옮기지 않는다(⑨ 의 API 평가에서 캐시를 켜고 다시 본다).

## 찾아본 질의

1. `LLM agent context compression increases tool calls retrieval cost trade-off`
2. `LLM proving absence "none" queries verify negative more search steps agent`
3. `summarized context handles agent retrieve on demand token savings evaluation arXiv 2025 2026`
4. `adaptive retrieval when to retrieve LLM self-knowledge unnecessary retrieval Self-RAG Adaptive-RAG`
5. `"What Does Context Compression Cost an Agent" interaction cost retrieval calls fact-preserving operator`
6. `"When Absence Is Evidence" CROWN-QA completeness-sensitive negative reasoning over-closure retrieved context`
7. `"Token Reduction Is Not Cost Reduction" agents context compression cache pricing trajectory`
8. `"FOCUS" decision-preserving context compression LLM agents training-free`

## 아직 못 지운 가능성 · 못 본 곳

- **Liu (2608.16370)와 Beyond Token Savings (2609.32961)가 과업 종류(있음 · 없음)로 이미 갈라 봤을 수 있다.** 그러면 F2b 의 기여는 재현만 남는다. 전문을 읽어야 지울 수 있는데 여기서는 arxiv 가 막혀 있다.
- Liu 의 '환경에 달렸다'(ALFWorld 에서 급증 없음)를 우리 H1 이 설명할 수도, 못 할 수도 있다. ALFWorld 과업의 '없음 확인' 비율을 모른다.
- 정보 검색 · 데이터베이스 쪽의 '닫힌 세계 가정 · 완전성' 문헌(open/closed world, completeness of answers)은 보지 않았다.
- 한국어 · 일본어 문헌, 산업 블로그(Anthropic · OpenAI 의 맥락 관리 글)는 보지 않았다.
