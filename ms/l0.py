"""L0 Telemetry(cogito5170/Telemetry) -- **선택 의존**. MS 런타임이 "무슨 일이 일어났나" 를 L0 사건으로 바로 낸다.

    pip install git+https://github.com/cogito5170/Telemetry

    recorder(run_id, ledger_path=None, sink=None)  -> telemetry.Recorder | NullRecorder
        ledger_path · sink 를 안 주면 NullRecorder(아무것도 안 낸다). 주었는데 L0 가 없으면 ImportError -- 조용히 안 버린다.

내는 사건(한 실행 = 한 run_id = RunRecord.run.run_id):
    run.start                         모형 · provider
    llm.request → llm.response        모형 호출마다(Pipeline._call). 사용량은 MS canonical(OTel 꼴) 그대로 -- 캐시 밖 입력을 지어내지 않는다
                | llm.error           provider 예외면 예외 종류 · HTTP 상태 · 본문(있을 때만). 메시지는 안 남긴다
    tool.start → tool.end             ALLOW 가지에서 도구가 불릴 때마다(Pipeline 의 유일한 도구 호출 자리). 경과는 단조 시계로
    run.end                           끝난 까닭(Pipeline outcome) · 회전 수 · 벽시계 · provider 보고 비용 · decision_ref

**L0 에 가지 않는 것**: 정책이 본 상태 · 맥락/프롬프트 계획 · 중재 결정(ALLOW/DENY 와 규칙) · 맥락 글. 그것은 결정 기록
(ms/decision_record.py)에 있고 `run.end.decision_ref` 로만 잇는다. 단가표 비용 · 추정 토큰도 L0 가 아니다(RunRecord 에만).
"""
from __future__ import annotations

import importlib
from contextlib import contextmanager

# MS provider 이름 -> L0 오류 대응표 이름. 표가 없는 provider(sim-* 등)는 번역하지 않고 원래 값만 남는다
ERROR_TABLE = {"claude": "anthropic", "claude-cli": "anthropic", "openai": "openai", "gemini": "gemini"}


def _l0():
    try:
        t = importlib.import_module("telemetry")
    except ImportError:
        return None
    return t if str(getattr(t, "SPEC", "")).startswith("l0-telemetry/") else None


def available() -> bool:
    return _l0() is not None


def recorder(run_id: str, ledger_path=None, sink=None, source: str = "inproc:ms", wall=None):
    """wall: 사건 시각(unix ms)을 낼 시계 -- Runtime 이 자기 시계를 준다(CMD-M26). 없으면 Recorder 의 기본(벽시계)."""
    if ledger_path is None and sink is None:
        return NullRecorder()
    t = _l0()
    if t is None:
        raise ImportError("L0 Telemetry 가 없다 -- pip install git+https://github.com/cogito5170/Telemetry")
    from telemetry.ledger import JsonlSink
    return t.Recorder(run_id, sink if sink is not None else JsonlSink(ledger_path), source=source,
                      **({"wall": wall} if wall is not None else {}))


class _Nothing:
    def response(self, **kw):
        pass

    def result(self, output=None, **kw):
        pass


class NullRecorder:
    """L0 를 안 쓸 때. 같은 이름의 메서드가 아무것도 안 한다 -- 계측 자리의 코드가 한 갈래로 남게."""
    enabled = False

    def run_start(self, **kw):
        pass

    def run_end(self, **kw):
        pass

    @contextmanager
    def llm_call(self, *a, **kw):
        yield _Nothing()

    @contextmanager
    def tool(self, *a, **kw):
        yield _Nothing()

    @contextmanager
    def action(self, action_type, decision_ref=None, target=None, action_ref=None, args=None):
        """Recorder.action 과 같은 서명(CMD-T17) -- 실행기 길의 계측 자리가 L0 없이도 한 갈래로 남게."""
        yield _Nothing()
