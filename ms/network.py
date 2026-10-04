"""Distributed Session Network -- 피어 메시지 · 상호작용 가중치 pi (CMD-NET4 · POL-3 · NETWORK_SPEC 3 · 4 · 9 · 10).

새 기억을 만들지 않는다. 피어 메시지는 `PeerMessage` 모형의 행으로 `StateManager.ingest` 를 지나 그래프에 들어가고, 맥락은 기존
`ContextPolicy`(KEEP · SUMMARIZE · RETRIEVE · DROP · DEFER, 글자 예산)가 짓는다. pi_ij 는 **파생 상태**다 -- 상호작용 기록에서
순수 함수로 다시 계산하고, L0 필드가 아니다.

    pi = r * (w_u*u + w_g*g + w_t*t)            모든 성분은 [0, 1]
    u  쓸모    = (쓸모 있던 수 + 1) / (전체 + 2)        사전 1/2. 쓸모 = 받는 쪽 상태 전이가 있었고 나중에 무효가 되지 않음
    g  정보 이득 = mean( clip((불확실_전 - 불확실_후) / max(1, 불확실_전), 0, 1) )   기록이 없으면 0
    t  신뢰    = 1 - (i 의 DC 안 j 관측 중 무효 비율)     관측이 없으면 사전 0.5
    r  관련성  = |i 가 필요로 하는 ref ∩ j 가 내보내는 ref| / max(1, |필요|)
    감쇠       = 상호작용 · 관측마다 2^(-나이 / half_life) 를 가중으로 (u · g · t 모두)

기본 가중치는 w_u = w_g = w_t = 1/3, 창은 최근 N 개(`NetConfig.window`). 시계를 읽지 않는다 -- `now` 를 받는다(같은 입력 -> 같은 pi).
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Model
from .telemetry import Telemetry

PEER_MESSAGE = "PeerMessage"

PEER_MESSAGE_MODEL = {
    "name": PEER_MESSAGE,
    "properties": {
        "sender": {"type": "string"}, "receiver": {"type": "string"}, "msg_id": {"type": "string"},
        "in_reply_to": {"type": "string"}, "schema": {"type": "string"},
        "refs": {"type": "string"},                       # 정렬한 ref 를 쉼표로 이은 것 (결정론)
        "bytes": {"type": "integer", "min": 0},
    },
    "bindings": [{"signal": k, "property": k} for k in
                 ("sender", "receiver", "msg_id", "in_reply_to", "schema", "refs", "bytes")],
}


@dataclass(frozen=True)
class PeerMessage:
    sender: str
    receiver: str
    msg_id: str
    schema: str = ""
    refs: tuple = ()
    bytes: int = 0
    in_reply_to: str = ""
    ts: "float | None" = None


def peer_message_model() -> Model:
    return Model.from_dict(PEER_MESSAGE_MODEL)


def ingest_peer_message(manager, msg: PeerMessage) -> list:
    """피어 메시지를 그래프 행(개체 `peer:<msg_id>`)으로 들인다. 값마다 ingest 를 지난다 -- 검증에 지면 상태가 아니다."""
    if PEER_MESSAGE not in manager.models:
        manager.add_model(peer_message_model())
    nid = f"peer:{msg.msg_id}"
    manager.declare(nid, PEER_MESSAGE)
    vals = {"sender": msg.sender, "receiver": msg.receiver, "msg_id": msg.msg_id, "in_reply_to": msg.in_reply_to,
            "schema": msg.schema, "refs": ",".join(sorted(msg.refs)), "bytes": msg.bytes}
    return [manager.ingest(Telemetry(f"peer:{msg.sender}", nid, k, v, ts=msg.ts)) for k, v in vals.items()]


# -- pi ----------------------------------------------------------------------
@dataclass(frozen=True)
class NetConfig:
    window: int = 20                    # 간(edge) 마다 최근 N 개 상호작용
    half_life: float = 3600.0           # 초. 이 시간이 지나면 가중이 반으로
    weights: tuple = (1 / 3, 1 / 3, 1 / 3)   # (w_u, w_g, w_t) -- 기본 1/3 씩
    theta: float = 0.3                  # 활성 문턱

    def __post_init__(self):
        if self.window < 1 or self.half_life <= 0 or len(self.weights) != 3 or any(w < 0 for w in self.weights) \
                or sum(self.weights) > 1 + 1e-9:
            raise ValueError("NetConfig: window >= 1, half_life > 0, 가중치 3 개는 0 이상이고 합이 1 이하여야 한다")


@dataclass(frozen=True)
class Interaction:
    """간 i->j 위의 상호작용 하나."""
    ts: float
    transition: bool = False            # 받는 쪽 상태 전이가 있었나
    invalidated: bool = False           # 그 전이가 나중에 무효가 되었나
    uncertain_before: int = 0
    uncertain_after: int = 0
    verify: bool = False                # 검증 교환 -- 풀릴 때까지 pi 에 안 센다

    @property
    def useful(self) -> bool:
        return self.transition and not self.invalidated


@dataclass(frozen=True)
class Observation:
    """i 의 DC 안에 있는 j 의 관측 하나."""
    ts: float
    invalid: bool = False


def decay(age: float, half_life: float) -> float:
    return 0.5 ** (max(0.0, age) / half_life)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def usefulness(inter, now, cfg: NetConfig) -> float:
    xs = [x for x in inter if not x.verify][-cfg.window:]
    w = [decay(now - x.ts, cfg.half_life) for x in xs]
    return (sum(wi for wi, x in zip(w, xs) if x.useful) + 1.0) / (sum(w) + 2.0)


def info_gain(inter, now, cfg: NetConfig) -> float:
    xs = [x for x in inter if not x.verify][-cfg.window:]
    w = [decay(now - x.ts, cfg.half_life) for x in xs]
    if not xs or sum(w) == 0:
        return 0.0
    gains = [_clip((x.uncertain_before - x.uncertain_after) / max(1, x.uncertain_before)) for x in xs]
    return _clip(sum(wi * g for wi, g in zip(w, gains)) / sum(w))


def trust(observations, now, cfg: NetConfig) -> float:
    xs = list(observations)[-cfg.window:]
    w = [decay(now - x.ts, cfg.half_life) for x in xs]
    if not xs or sum(w) == 0:
        return 0.5                                          # 사전
    return _clip(1.0 - sum(wi for wi, x in zip(w, xs) if x.invalid) / sum(w))


def relevance(required, exported) -> float:
    req = set(required)
    return len(req & set(exported)) / max(1, len(req))


def pi(inter, observations, required, exported, now, cfg: NetConfig = NetConfig()) -> float:
    wu, wg, wt = cfg.weights
    r = relevance(required, exported)
    return _clip(r * (wu * usefulness(inter, now, cfg) + wg * info_gain(inter, now, cfg)
                      + wt * trust(observations, now, cfg)))


# -- 활성 ----------------------------------------------------------------------
class VerifyFlags:
    """`contradicts` 관계가 세우는 검증 깃발. 열림 -> 보냄 -> 풀림. 깃발마다 교환은 한 번 -- 문턱 아래여도 보내되 pi 는 안 올린다."""

    def __init__(self):
        self._s: dict = {}

    def open(self, i, j):
        if self._s.get((i, j)) is None:
            self._s[(i, j)] = "open"

    def is_open(self, i, j) -> bool:
        return self._s.get((i, j)) == "open"

    def sent(self, i, j):
        if self.is_open(i, j):
            self._s[(i, j)] = "sent"

    def resolve(self, i, j):
        """풀린다. 결과는 보통 상호작용(`verify=False`)으로 따로 적는다 -- 그때부터 pi 에 센다."""
        self._s.pop((i, j), None)

    def state(self, i, j):
        return self._s.get((i, j))


def contradicting_peers(graph, owner: dict, me: str, rel: str = "contradicts") -> list:
    """그래프의 `contradicts` 간에서, 내 개체와 **다른 피어**의 개체가 맞서는 피어들(정렬)."""
    out = set()
    for r, a, b in graph.edges:
        if r != rel:
            continue
        pa, pb = owner.get(a), owner.get(b)
        if pa == me and pb not in (None, me):
            out.add(pb)
        elif pb == me and pa not in (None, me):
            out.add(pa)
    return sorted(out)


def activate(i, j, pi_ij: float, cfg: NetConfig, *, missing=(), covers=None, flags: "VerifyFlags | None" = None):
    """(보낼까, 까닭). 까닭: pi · required · verify · none.

    required: i 의 정책이 필요로 하는 ref 가 빠졌고 그것을 덮는 피어가 j **하나뿐**.
    verify:   j 와의 `contradicts` 깃발이 열려 있다 -- 문턱 아래여도 검증 교환 한 번.
    """
    if pi_ij >= cfg.theta:
        return True, "pi"
    covers = covers or {}
    for ref in sorted(missing):
        if [p for p, refs in sorted(covers.items()) if ref in refs] == [j]:
            return True, "required"
    if flags is not None and flags.is_open(i, j):
        return True, "verify"
    return False, "none"
