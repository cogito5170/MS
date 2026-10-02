"""Arbiter -- LLM 의 제안을 결정론적으로 ALLOW / DENY 한다. LLM 이 자기 제안을 판정하지 않는다.

중재자는 두 가지를 같이 본다: LLM 이 **본 것**(최소 맥락)과 **지금의 상태**(그래프).
규칙(위에서부터 처음 걸리는 것이 DENY 의 까닭):

    A0  제안을 못 읽었다(JSON 아님 · tool/target 모양 틀림)
    A1  맥락에서 제안되지 않은 도구
    A2  근거 없음 -- 대상이 KEEP 으로 본 개체가 아니다(요약 · 손잡이로만 봤으면 retrieve 먼저).
        retrieve 면 그 handle 이 이 맥락에 있어야 한다
    A3  그 도구의 targets 에 없는 대상
    A4  인자가 도구의 params 와 안 맞는다
    A5  맥락을 지은 뒤 대상의 상태가 바뀌었다(본 판 != 지금 판) -- 낡은 그림을 보고 한 제안이다
    A6  사전조건이 보는 속성이 없거나 낡았다(ttl) · 사전조건이 지금 상태에서 거짓
    A7  external · irreversible 인데 허가(grants)가 없다 -- 기본은 DENY
    A8  같은 대상 · 같은 판에서 이미 ALLOW 한 같은 제안(되풀이)
    E   중재자 안에서 예외 -- **DENY** (도구 쪽은 닫힌 쪽으로 틀린다)

`tool="none"` 은 할 것이 없다는 제안이라 판정 없이 NOOP 이다.

지키는 것: 바깥 · 되돌릴 수 없는 동작은 허가된 것만 · 결과는 결정론적 확인을 지나야 · 확신이 없으면 DENY 하고 까닭을 LLM 에 돌려준다.
(계획 ⑦: 허가 A7 은 Guard 로 옮긴다 -- 이 층은 선택한 제안이 맥락 · 상태에 맞는지를, Guard 는 실행해도 되는지를 본다.)
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from .llm import NONE
from .query import unmet
from .tools import RETRIEVE

ALLOW, DENY, NOOP = "ALLOW", "DENY", "NOOP"


@dataclass
class Decision:
    verdict: str
    rule: str
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Arbiter:
    def __init__(self, registry, grants=(), ledger_path: "str | None" = None, clock=time.time):
        self.registry = registry
        self.grants = set(grants)          # external · irreversible 을 허락한 도구 이름
        self.ledger: list = []
        self.ledger_path = ledger_path
        self.clock = clock
        self._allowed: set = set()

    def decide(self, proposal, ctx, manager) -> Decision:
        try:
            d = self._decide(proposal, ctx, manager)
        except Exception as e:                          # 닫힌 쪽으로
            d = Decision(DENY, "E", [f"중재자 예외: {type(e).__name__}: {e}"])
        self._log(proposal, d)
        return d

    def _decide(self, p, ctx, m) -> Decision:
        if p.error:
            return Decision(DENY, "A0", [p.error])
        if p.tool == NONE:
            return Decision(NOOP, "-", ["할 것이 없다는 제안"])
        if p.tool == RETRIEVE:
            if p.target not in ctx.handles:
                return Decision(DENY, "A2", [f"handle {p.target} 는 이 맥락에 없다"])
            return Decision(ALLOW, "R", [f"{p.target}: {ctx.handles[p.target]['count']} 행을 다음 판에"])
        offer = next((o for o in ctx.offers if o["tool"] == p.tool), None)
        if offer is None:
            return Decision(DENY, "A1", [f"도구 {p.tool} 는 이 맥락에서 제안되지 않았다"])
        if p.target not in ctx.seen:
            how = ctx.decisions.get(p.target)
            if how in ("SUMMARIZE", "RETRIEVE", "DEFER"):
                h = next((k for k, v in ctx.handles.items() if p.target in v["ids"]), "?")
                return Decision(DENY, "A2", [f"{p.target} 는 요약 · 손잡이로만 봤다 -- retrieve {h} 먼저"])
            if how == "DROP":
                return Decision(DENY, "A2", [f"{p.target} 는 맥락 정책이 뺐다(DROP) -- LLM 이 본 적 없다"])
            return Decision(DENY, "A2", [f"{p.target} 는 LLM 이 본 적 없는 개체다"])
        if p.target not in offer["targets"]:
            return Decision(DENY, "A3", [f"{p.target} 는 {p.tool} 의 대상이 아니다"])
        tool = self.registry.get(p.tool)
        bad = tool.check_args(p.args)
        if bad:
            return Decision(DENY, "A4", bad)
        node = m.graph.nodes.get(p.target)
        if node is None:
            return Decision(DENY, "A5", [f"{p.target} 가 지금 그래프에 없다"])
        if node.version != ctx.seen[p.target]:
            return Decision(DENY, "A5", [f"{p.target} 가 맥락을 지은 뒤 바뀌었다(판 {ctx.seen[p.target]} -> {node.version})"])
        why = unmet(tool, p.target, m)
        if why:
            return Decision(DENY, "A6", why)
        if tool.risk in ("external", "irreversible") and tool.name not in self.grants:
            return Decision(DENY, "A7", [f"{tool.name} 는 {tool.risk} 인데 허가가 없다"])
        k = (p.key(), node.version)
        if k in self._allowed:
            return Decision(DENY, "A8", [f"같은 제안을 {p.target} 판 {node.version} 에서 이미 ALLOW 했다"])
        self._allowed.add(k)
        return Decision(ALLOW, "0", [f"{tool.name}({tool.risk}) -> {p.target}"])

    def _log(self, p, d):
        rec = {"ts": self.clock(), "proposal": p.to_dict(), **d.to_dict()}
        self.ledger.append(rec)
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
