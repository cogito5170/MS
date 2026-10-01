# 선행조사 -- Model-State 층 (2026-10-01)

코드보다 먼저 커밋한다. 짓는 것:

    TELEMETRY -> State Manager -> MODEL + RELATIONSHIP -> STATE GRAPH
      -> STATE QUERY + TOOL QUERY -> CONTEXT POLICY(KEEP · SUMMARIZE · RETRIEVE)
      -> MINIMAL CONTEXT -> LLM -> PROPOSAL -> WALP ARBITER(ALLOW · DENY) -> TOOL

원칙 셋: (1) Telemetry 는 State 가 아니다 (2) State 의 뜻은 Model 이 정한다 (3) LLM 에는 State 전체가 아니라 Query 결과만 준다.

**이 조사는 얕다.** 아래 인용은 전부 검색 조각만 보고 단 것이다(`[출처:조각]`). 전문을 읽은 것은 하나도 없다.

## 가장 가까운 선행연구

| 우리 설계의 부분 | 가장 가까운 것 | 확인수준 | 겹치는 것 · 다른 것 |
|---|---|---|---|
| **Model · Relationship · State Graph · Query** (왼쪽 절반 전체) | Azure Digital Twins -- DTDL 모형(속성 · 관계) · twin graph · SQL 꼴 질의(`JOIN ... RELATED`). learn.microsoft.com/azure/digital-twins | [출처:조각] | **거의 그대로 겹친다.** 모형이 상태의 뜻을 정하고, 관계로 그래프를 짓고, 질의로 꺼낸다. 다른 것: LLM 과 집행기 쪽이 없다. 우리 것은 표준 라이브러리 장난감 크기다 |
| Telemetry ≠ State | W3C SOSA/SSN -- `sosa:Observation` 은 FeatureOfInterest 의 ObservableProperty 를 **추정하는 행위**이지 속성 자체가 아니다. w3.org/TR/vocab-ssn | [출처:조각] | 원칙 1 의 개념 구분이 여기 이미 있다. 제어공학의 관측 z 와 상태 x 의 구분(관측모형 h(x))도 같은 것이다 |
| **Proposal -> 결정론적 집행 -> Allow/Deny** | CaMeL (arXiv:2503.18813, SaTML 2026) -- 특권 LLM 이 계획, 맞춤 인터프리터가 **도구 호출마다 보안 정책을 집행** | [출처:조각] | 중재자 자리와 같다. CaMeL 은 데이터 출처(provenance)와 capability 로 막는다. 우리는 상태 그래프에 대한 사전조건 · 신선도 · 근거(본 것만 건드림)로 막는다 |
| 도구 이름 · 인자에 대한 기호 규칙 | Progent (arXiv:2504.11703) -- 도구 호출마다 결정론적 정책 검사, 정책 확장은 승인 필요(SMT 로 좁힘/넓힘 판정) | [출처:조각] | 우리 위험 등급 허용목록과 같은 부류. 우리는 SMT 가 없다 |
| 실행 시점 규칙 집행 DSL | AgentSpec (arXiv:2503.18666, ICSE'26) -- trigger · predicate · enforcement 규칙 | [출처:조각] | 사전조건 술어로 막는 것이 같다 |
| **KEEP · SUMMARIZE · RETRIEVE** | MemGPT (arXiv:2310.08560) -- 맥락 창을 주기억, 바깥을 디스크로 보고 **LLM 이 함수 호출로** 들이고 내보낸다 | [출처:조각] | 세 동작이 같다. 다른 것: MemGPT 는 LLM 이 고르고, 우리는 **결정론적 정책**이 고른다(LLM 은 RETRIEVE 손잡이만 청할 수 있고 그것도 중재자를 지난다) |
| 디지털 트윈 + 맥락 공학 | DT-MDP-CE (arXiv:2603.22083, 2026, IBM) -- 에이전트 추론을 유한 MDP 로 추상화, 역강화학습 정책으로 맥락을 고친다 | [출처:조각] | "트윈 위에서 맥락을 고른다" 가 같다. 우리는 학습하지 않는다 |
| LLM + 그래프 트윈 | Graph-DT-GPT (ScienceDirect S0926580526000324) -- 질의 생성 에이전트가 그래프 트윈에 묻고 답을 그래프에 근거시킨다 | [출처:조각] | 다른 것: 거기서는 **LLM 이 질의를 짓는다.** 우리 그림에서는 질의가 LLM **앞**에 있다 -- LLM 은 질의 결과만 받는다 |

## 우리가 다른 점 (주장이 아니라 범위)

1. **새 방법이 아니다.** 디지털 트윈(왼쪽) + 맥락 정책(가운데) + 정책 집행 중재자(오른쪽)를 한 패키지로 묶은 공학이다.
2. 세 원칙을 **말이 아니라 시험으로** 붙든다: 모형에 안 묶인 텔레메트리는 그래프에 못 들어간다 · 같은 텔레메트리가 모형에 따라 다른 상태가 된다 ·
   LLM 이 받는 글에 질의 밖 개체가 한 글자도 없다.
3. 중재자는 LLM 이 **본 것**(최소 맥락)과 **지금의 상태**(그래프)를 둘 다 본다 -- 본 적 없는 개체 · 제안되지 않은 도구 · 맥락을 지은 뒤 바뀐 상태(TOCTOU) ·
   낡은 텔레메트리 · 허가 없는 되돌릴 수 없는 동작은 막는다.
4. 도구의 결과도 텔레메트리로 되돌아간다(원칙 1 을 루프 끝까지).

## 찾아본 질의

- `CaMeL Defeating Prompt Injections by Design arXiv LLM proposes capability policy enforcement`
- `Azure Digital Twins DTDL models relationships twin graph query language`
- `AgentSpec runtime enforcement LLM agents arXiv 2503.18666`
- `MemGPT LLMs as operating systems arXiv 2310.08560 context paging`
- `SOSA SSN ontology W3C Observation ObservableProperty FeatureOfInterest`
- `digital twin knowledge graph LLM context query state graph agent minimal context 2025 arXiv`
- `Progent programmable privilege control LLM agents arXiv 2504.11703 tool call policy allow deny`

## 아직 못 지운 가능성

- **Palantir Ontology + AIP 가 이 그림 전체를 이미 상품으로 갖고 있을 가능성이 크다**(객체 · 링크 · Action 의 제출 조건 · LLM 에 온톨로지 범위 도구).
  **안 찾아봤다.** 찾으면 이 패키지는 그것의 작은 공개 재현이 된다.
- Eclipse Ditto · Asset Administration Shell(산업 4.0) · OPA(Open Policy Agent)를 안 봤다. 상태 그래프와 정책 집행 각각에서 더 가까울 수 있다.
- LangGraph 의 상태 객체 · checkpoint 를 안 봤다(이름은 같지만 Model 이 상태의 뜻을 정하는 구조인지 모른다).
- 맥락 압축 계열(LLMLingua 등)과 **잰 비교가 없다.** 우리 SUMMARIZE 는 결정론적 집계(개수 · 최소 · 최대 · 평균 · 값 분포)일 뿐이다.
- 이 패키지는 **잰 것이 없다.** 토큰을 얼마나 아끼는지, 막아야 할 것을 얼마나 막는지 모른다. 예시의 크기 비교는 장난감 세계 하나의 산수다.
