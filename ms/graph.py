"""State Graph -- 모형으로 해석된 상태만 산다.

노드마다 속성 값에 **출처**(어느 텔레메트리 · 어느 파생)와 시각이 붙는다. 노드는 바뀔 때마다 판(version)이 오르고,
중재자는 LLM 이 본 판과 지금 판을 견준다(맥락을 지은 뒤 바뀐 상태를 막는다).

이 클래스에는 일부러 "전부를 글로" 내는 메서드가 없다. LLM 쪽으로 가는 길은 `query.StateQuery` 뿐이다(원칙 3).
`dump()` 는 시험 · 디버그용이고 맥락 쪽 코드는 그것을 부르지 않는다(시험이 붙든다).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Value:
    value: object
    ts: float              # 관측 시각(파생이면 입력 중 가장 오래된 것)
    src: str               # 텔레메트리 id 또는 "derived:<입력들>"
    derived: bool = False


@dataclass
class Node:
    id: str
    model: str
    props: dict = field(default_factory=dict)    # 이름 -> Value
    version: int = 0

    def values(self) -> dict:
        return {k: v.value for k, v in self.props.items()}


class StateGraph:
    def __init__(self):
        self.nodes: dict = {}
        self.edges: set = set()      # (관계, 출발, 도착)
        self.version = 0

    def _bump(self, node: Node):
        node.version += 1
        self.version += 1

    def add_node(self, nid: str, model: str) -> Node:
        n = Node(nid, model)
        self.nodes[nid] = n
        self._bump(n)
        return n

    def set_prop(self, nid: str, prop: str, val: Value):
        n = self.nodes[nid]
        old = n.props.get(prop)
        n.props[prop] = val
        if old is None or old.value != val.value:
            self._bump(n)
        return old

    def drop_prop(self, nid: str, prop: str):
        n = self.nodes[nid]
        if n.props.pop(prop, None) is not None:
            self._bump(n)

    def add_edge(self, rel: str, a: str, b: str):
        if (rel, a, b) not in self.edges:
            self.edges.add((rel, a, b))
            self._bump(self.nodes[a])
            self._bump(self.nodes[b])

    def out(self, nid: str, rel: "str | None" = None):
        return [b for (r, a, b) in self.edges if a == nid and (rel is None or r == rel)]

    def inn(self, nid: str, rel: "str | None" = None):
        return [a for (r, a, b) in self.edges if b == nid and (rel is None or r == rel)]

    def dump(self) -> dict:
        """시험 · 디버그 전용. 맥락을 짓는 코드는 이것을 부르지 않는다."""
        return {"nodes": {k: {"model": n.model, "version": n.version,
                              "props": {p: v.value for p, v in n.props.items()}} for k, n in self.nodes.items()},
                "edges": sorted(self.edges)}
