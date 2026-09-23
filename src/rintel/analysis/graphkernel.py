"""GraphKernel — rustworkx-backed graph algorithms (FLOW1-ANALYZER0 §16).

Boundary rule (frozen): rustworkx (PyDiGraph) identity must NOT leak into
the domain.  All algorithms take/return domain-shaped inputs (simple
id/keywords) and convert inside this module.

rustworkx is deliberately NOT part of the analyzer tournament — it is the
settled graph back-end; this module is its minimal adapter.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

__all__ = ["GraphKernel", "GraphEdge", "GraphResult"]

import rustworkx as rx


class GraphEdge:
    __slots__ = ("source", "target", "kind", "weight", "metadata")

    def __init__(self, source: str, target: str, kind: str = "edge",
                 weight: float = 1.0, metadata: Optional[dict] = None):
        self.source = source
        self.target = target
        self.kind = kind
        self.weight = weight
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target,
                "kind": self.kind, "weight": self.weight,
                "metadata": self.metadata}


class GraphResult:
    """Domain-shaped algorithm result (no rustworkx types inside)."""

    def __init__(self, kind: str, *, items: Optional[list] = None,
                 value: Any = None, meta: Optional[dict] = None):
        self.kind = kind
        self.items = items or []
        self.value = value
        self.meta = meta or {}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "items": self.items, "value": self.value,
                "meta": self.meta}


class GraphKernel:
    """Builds a PyDiGraph from edges and runs the frozen algorithm set."""

    def __init__(self) -> None:
        self._graph: Optional[rx.PyDiGraph] = None
        self._index: dict[str, int] = {}

    # -- construction --------------------------------------------------------
    def build(self, edges: Iterable[GraphEdge]) -> "GraphKernel":
        g = rx.PyDiGraph()
        index: dict[str, int] = {}
        for e in edges:
            for nid in (e.source, e.target):
                if nid not in index:
                    index[nid] = g.add_node(nid)
            g.add_edge(index[e.source], index[e.target],
                       {"kind": e.kind, "weight": e.weight,
                        "metadata": e.metadata})
        self._graph = g
        self._index = index
        return self

    def from_edges(self, edges: Iterable[tuple[str, str, str]]) -> "GraphKernel":
        """Convenience: (source, target, kind)."""
        return self.build(GraphEdge(s, t, k) for s, t, k in edges)

    @property
    def graph(self) -> Optional[rx.PyDiGraph]:
        return self._graph

    def node_count(self) -> int:
        return len(self._index) if self._graph is not None else 0

    def edge_count(self) -> int:
        return self._graph.num_edges() if self._graph is not None else 0

    # -- algorithms (all domain-result shaped) -------------------------------
    def scc(self) -> GraphResult:
        """Strongly connected components (list of id-sets)."""
        if self._graph is None:
            raise RuntimeError("graph not built")
        comps = rx.strongly_connected_components(self._graph)
        return GraphResult(
            "scc",
            items=[[self._index_name(ix) for ix in comp] for comp in comps])

    def detect_cycles(self) -> GraphResult:
        """Cycle detection: list of member ids per back-edge cycle."""
        comps = self.scc().items
        cyclic = [c for c in comps if len(c) > 1]
        return GraphResult("cycles", items=cyclic)

    def topo_sort(self) -> GraphResult:
        """Topological order (id list); raises on cycles like rustworkx."""
        if self._graph is None:
            raise RuntimeError("graph not built")
        order = rx.topological_sort(self._graph)
        return GraphResult("topo", items=[self._index_name(ix)
                                          for ix in order])

    def dependency_cone(self, node_id: str) -> GraphResult:
        """All ancestors (dependencies) of `node_id` (incl. itself)."""
        idx = self._index.get(node_id)
        if idx is None or self._graph is None:
            return GraphResult("cone", items=[])
        ancestors = set()
        for ix in range(self._graph.num_nodes()):
            if ix != idx and idx in rx.descendants(self._graph, ix):
                ancestors.add(ix)
        members = {idx} | ancestors
        return GraphResult("cone", items=sorted(
            [self._index_name(ix) for ix in members]))

    def transitive_reduction(self) -> GraphResult:
        """Transitive reduction edge list (id pair + kind)."""
        if self._graph is None:
            raise RuntimeError("graph not built")
        pair_kind: dict[tuple[int, int], str] = {}
        for s, t, data in self._graph.weighted_edge_list():
            pair_kind[(s, t)] = str(data.get("kind"))
        # reachability matrix
        n = self._graph.num_nodes()
        reach = [[False] * n for _ in range(n)]
        for i in range(n):
            stack = list(self._graph.successor_indices(i))
            while stack:
                j = stack.pop()
                if not reach[i][j]:
                    reach[i][j] = True
                    stack.extend(self._graph.successor_indices(j))
        kept = []
        for (s, t), kind in pair_kind.items():
            # drop edge s->t if another path of length >= 2 reaches t
            redundant = False
            for m in self._graph.successor_indices(s):
                if m == t:
                    continue
                if reach[m][t]:
                    redundant = True
                    break
            if not redundant:
                kept.append({"source": self._index_name(s),
                             "target": self._index_name(t), "kind": kind})
        return GraphResult("transitive_reduction", items=kept)

    def longest_path(self, weight_node: bool = False) -> GraphResult:
        """Longest path (DAG) as (length, id list); empty on cycles."""
        if self._graph is None:
            raise RuntimeError("graph not built")
        try:
            order = rx.topological_sort(self._graph)
        except rx.DAGHasCycle:
            return GraphResult("longest_path", items=[], meta={"cyclic": True})
        edge_w: dict[tuple[int, int], float] = {}
        for s, t, data in self._graph.weighted_edge_list():
            edge_w[(s, t)] = float(data.get("weight", 1.0))
        best: dict[int, tuple[float, list[int]]] = {}
        for ix in order:
            best[ix] = (0.0, [ix])
            for pred in self._graph.predecessor_indices(ix):
                dist, path = best[pred]
                cand = (dist + edge_w[(pred, ix)], path + [ix])
                if cand[0] > best[ix][0]:
                    best[ix] = cand
        best_ix = max(best, key=lambda k: best[k][0])
        dist, path = best[best_ix]
        return GraphResult("longest_path",
                           items=[self._index_name(p) for p in path],
                           value=dist)

    # -- helpers -------------------------------------------------------------
    def _index_name(self, ix: int) -> str:
        inv = {v: k for k, v in self._index.items()}
        return inv[ix]
