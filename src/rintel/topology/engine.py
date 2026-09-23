"""TOPO-ENGINE0 Function Topology engine (Phase 1–5).

AnalysisFact → Hard Relations → Function Topology.
- Nodes carry canonical identity (T1); provider metadata is provenance only.
- CALL/DATA/STATE/CONTROL/RESOURCE/TIME hard-edge kinds; witness_count +
  representative witnesses (Phase 3 witness preservation); every edge
  supports drill-down to source (Phase 3).
- Truth layers T0 (OBSERVED / source evidence) and T1 (deterministic
  derived) only — T2 interface reserved, T3 forbidden (Phase 2).
- `[Unknown Dynamic Target]` is a placeholder node kind — NEVER a
  canonical Function (T4).
- File projection keeps cross-file edges (T6 boundary preservation);
  Module projection uses DECLARED membership only (no clustering).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Optional

from ..analysis.contract import (AnalysisFact, FactKind, SemanticKind,
                                 TargetResolution, TruthClass)
from ..analysis.binding import BindingStatus, CanonicalSymbolBinding

UNKNOWN_DYNAMIC = "[Unknown Dynamic Target]"
RESOURCE_NAMES = {"filesystem", "database", "gpu", "network", "time",
                  "stdout", "stderr"}


class HardEdgeKind(str, Enum):
    CALL = "CALL"
    DATA = "DATA"
    STATE = "STATE"
    CONTROL = "CONTROL"
    RESOURCE = "RESOURCE"
    TIME = "TIME"


class TruthLayer(str, Enum):
    T0 = "T0"   # OBSERVED / source evidence
    T1 = "T1"   # deterministic derived
    T2 = "T2"   # runtime observed (interface reserved, unused)
    # T3 forbidden (suggested) — no enum value on purpose


@dataclass
class FunctionNode:
    canonical_symbol_id: str
    name: str
    language: str
    file: str
    source_range: dict
    snapshot_id: Optional[str]
    binding: CanonicalSymbolBinding
    control_landmarks: list[dict] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_symbol_id": self.canonical_symbol_id,
            "name": self.name, "language": self.language,
            "file": self.file, "source_range": self.source_range,
            "snapshot_id": self.snapshot_id,
            "binding_status": self.binding.binding_status.value,
            "control_landmarks": list(self.control_landmarks),
            "resources": list(self.resources),
            "provenance": {"provider": self.provenance.get("provider"),
                           "provider_version": self.provenance.get("provider_version"),
                           "provider_symbol": self.provenance.get("provider_symbol")},
        }


@dataclass
class TopologyEdge:
    kind: HardEdgeKind
    source: str          # canonical id or placeholder or resource name
    target: str
    truth_layer: TruthLayer
    truth_class: TruthClass
    execution_modality: str
    target_resolution: str
    coverage: str
    witnesses: list[dict]
    guard: Optional[str] = None
    data: Optional[str] = None
    source_span: Optional[dict] = None

    @property
    def witness_count(self) -> int:
        return len(self.witnesses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source": self.source, "target": self.target,
            "truth_layer": self.truth_layer.value,
            "truth_class": self.truth_class.value,
            "execution_modality": self.execution_modality,
            "target_resolution": self.target_resolution,
            "coverage": self.coverage,
            "witness_count": self.witness_count,
            "representative_witnesses": self.witnesses[:3],
            "guard": self.guard, "data": self.data,
            "source_span": self.source_span,
        }


@dataclass
class FunctionTopology:
    nodes: dict[str, FunctionNode]
    edges: list[TopologyEdge]
    placeholders: set[str]
    resources: set[str]
    bound_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "placeholders": sorted(self.placeholders),
            "resources": sorted(self.resources),
            "bound_count": self.bound_count,
        }


def _layer(fact: AnalysisFact) -> TruthLayer:
    """T0 vs T1 (Phase 2): OBSERVED/RESOLVED with exact target = source
    evidence; anything partially resolved/dynamic = deterministic derived."""
    if fact.truth_class in (TruthClass.OBSERVED, TruthClass.RESOLVED) \
            and fact.target_resolution is TargetResolution.EXACT:
        return TruthLayer.T0
    return TruthLayer.T1


def _resource_of(callee: str) -> Optional[str]:
    c = (callee or "").lower()
    if "printf" in c or "fprintf" in c or "fopen" in c or "open(" in c \
            or c.startswith("fwrite") or c.startswith("fread"):
        return "FileSystem"
    if "insert" in c or "select" in c or "sql" in c or "db_" in c:
        return "Database"
    if "socket" in c or "send" in c or "recv" in c or "http" in c:
        return "Network"
    if "gpu" in c or "cuda" in c:
        return "GPU"
    return None


class FunctionTopologyBuilder:
    """Builds the Function topology from AnalysisFacts + bindings."""

    def __init__(self, facts: list[AnalysisFact],
                 bindings: dict[str, CanonicalSymbolBinding]):
        self.facts = facts
        self.bindings = bindings
        self.nodes: dict[str, FunctionNode] = {}
        self.edges: list[TopologyEdge] = []
        self.placeholders: set[str] = set()
        self.resources: set[str] = set()
        self.bound_count: int = 0

    # -- node side ------------------------------------------------------------
    def _bind(self, provider_symbol: str) -> CanonicalSymbolBinding:
        return self.bindings.get(provider_symbol) or self.bindings.get(
            provider_symbol.split(":", 1)[-1] if ":" in provider_symbol
            else provider_symbol, None)

    def _bind_fact(self, f) -> Optional[CanonicalSymbolBinding]:
        """Binding lookup with the file-identity fallback (local-binding
        facts carry short function names + source_location.file)."""
        b = self._bind(f.subject)
        if b is not None:
            return b
        loc = getattr(f, "source_location", None)
        if loc is not None:
            fpath = getattr(loc, "file", None) or (
                loc.get("file") if isinstance(loc, dict) else None)
            if fpath:
                return self.bindings.get(f"{fpath}:{f.subject}") or \
                    self.bindings.get(f"{fpath}:{f.subject.strip()}")
        return None

    def _add_node_from_binding(self, b: CanonicalSymbolBinding,
                               fact: AnalysisFact) -> Optional[str]:
        if b is None or b.binding_status is not BindingStatus.EXACT:
            return None
        if b.canonical_symbol_id in self.nodes:
            return b.canonical_symbol_id
        n = FunctionNode(
            canonical_symbol_id=b.canonical_symbol_id,
            name=b.canonical_name or "", language=b.language,
            file=b.file, source_range={"file": b.file},
            snapshot_id=b.snapshot_id, binding=b,
            provenance={"provider": b.provider,
                        "provider_version": fact.provider_version,
                        "provider_symbol": b.provider_symbol})
        self.nodes[b.canonical_symbol_id] = n
        self.bound_count += 1
        return b.canonical_symbol_id

    def _target_node(self, fact: AnalysisFact) -> str:
        """Resolve CALL targets; never fabricate canonical identity."""
        if fact.target:
            b = self._bind(fact.target)
            if b and b.binding_status is BindingStatus.EXACT:
                nid = self._add_node_from_binding(b, fact)
                if nid:
                    return nid
            if fact.target_resolution is TargetResolution.UNKNOWN:
                self.placeholders.add(UNKNOWN_DYNAMIC)
                return UNKNOWN_DYNAMIC
            self.placeholders.add(UNKNOWN_DYNAMIC)
            return UNKNOWN_DYNAMIC
        return UNKNOWN_DYNAMIC

    # -- build ----------------------------------------------------------------
    def build(self) -> FunctionTopology:
        # nodes from SYMBOL facts bound to canonical identity
        for f in self.facts:
            if f.fact_kind is FactKind.SYMBOL and f.subject:
                b = self._bind(f.subject) or self._bind(
                    (f.subject.split(":<module>.")[-1]
                     if ":<module>." in f.subject else f.subject))
                if b:
                    self._add_node_from_binding(b, f)
        # edges
        for f in self.facts:
            if f.fact_kind is FactKind.CALL:
                self._call_edge(f)
            elif f.fact_kind is FactKind.DATA_FLOW:
                self._data_edge(f)
            elif f.fact_kind is FactKind.CONTROL_FLOW:
                self._control_landmark(f)
            elif f.fact_kind is FactKind.SIGNATURE:
                pass  # node metadata, not an edge
        # state edges: same-data shared access across functions
        self._state_edges()
        return FunctionTopology(nodes=self.nodes, edges=self.edges,
                                placeholders=self.placeholders,
                                resources=self.resources,
                                bound_count=self.bound_count)

    def _call_edge(self, f: AnalysisFact) -> None:
        src_exact = self._bind(f.subject)
        src = self._add_node_from_binding(src_exact, f) \
            if src_exact and src_exact.binding_status is BindingStatus.EXACT \
            else None
        if not src:
            return  # unbound caller → not a topology node (identity authority)
        tgt = self._target_node(f)
        res = _resource_of(f.target or "")
        if res:
            self.resources.add(res)
            self._node_resources(src, res)
            self.edges.append(TopologyEdge(
                HardEdgeKind.RESOURCE, src, res, _layer(f), f.truth_class,
                f.execution_modality.value, f.target_resolution.value,
                f.coverage.value,
                witnesses=[self._witness(f)], guard=f.guard,
                source_span=self._loc_dict(f.source_location)))
            return
        # dedupe CALL edges (same src/tgt/kind)
        for e in self.edges:
            if e.kind is HardEdgeKind.CALL and e.source == src \
                    and e.target == tgt:
                return
        self.edges.append(TopologyEdge(
            HardEdgeKind.CALL, src, tgt, _layer(f), f.truth_class,
            f.execution_modality.value, f.target_resolution.value,
            f.coverage.value,
            witnesses=[self._witness(f)], guard=f.guard,
            source_span=self._loc_dict(f.source_location)))

    def _node_resources(self, node: str, res: str) -> None:
        if node in self.nodes and res not in self.nodes[node].resources:
            self.nodes[node].resources.append(res)

    def _data_edge(self, f: AnalysisFact) -> None:
        # local-binding facts: subject=function, outputs/inputs carry data;
        # callee in metadata; consumer edges come from call facts with inputs
        meta = f.metadata
        binder = meta.get("binding")
        src = self._bind_fact(f)
        if not src or src.binding_status is not BindingStatus.EXACT:
            return
        src_id = self._add_node_from_binding(src, f)
        if not src_id:
            return
        callee = meta.get("callee")
        data = meta.get("data") or (f.outputs[0] if f.outputs else None)
        if data is None:
            return
        if binder == "call_result" and callee:
            # producer.return → local binding
            producer = self._bind(callee) or self._bind_fact(f)
            if producer and producer.binding_status is BindingStatus.EXACT:
                tgt = self._add_node_from_binding(producer, f)
                if tgt and tgt != src_id:
                    self.edges.append(TopologyEdge(
                        HardEdgeKind.DATA, tgt, src_id, _layer(f),
                        f.truth_class, f.execution_modality.value,
                        f.target_resolution.value, f.coverage.value,
                        witnesses=[self._witness(f)],
                        data=f"{callee}.result -> {data}",
                        source_span=self._loc_dict(f.source_location)))
        elif binder == "call" and f.inputs:
            # local → consumer parameter
            called = meta.get("callee")
            consumer = self._bind(called) or self._bind_fact(f)
            if consumer and consumer.binding_status is BindingStatus.EXACT:
                tgt = self._add_node_from_binding(consumer, f)
                if tgt and tgt != src_id:
                    self.edges.append(TopologyEdge(
                        HardEdgeKind.DATA, src_id, tgt, _layer(f),
                        f.truth_class, f.execution_modality.value,
                        f.target_resolution.value, f.coverage.value,
                        witnesses=[self._witness(f)],
                        data=f"{data} -> {called}.arg",
                        source_span=self._loc_dict(f.source_location)))

    def _control_landmark(self, f: AnalysisFact) -> None:
        """T5: IF/SWITCH/LOOP guard+scope survive on the function node;
        control operations stay in the underlying operation network."""
        src = self._bind_fact(f)
        if not src or src.binding_status is not BindingStatus.EXACT:
            return
        nid = self._add_node_from_binding(src, f)
        if not nid:
            return
        self.nodes[nid].control_landmarks.append({
            "semantic_kind": f.semantic_kind.value,
            "guard": f.guard,
            "scope": f.scope,
            "source": self._loc_dict(f.source_location),
            "provider": f.provider,
        })

    def _state_edges(self) -> None:
        # deterministic: same data name read/written by >=2 functions
        writes: dict[str, set[str]] = {}
        for f in self.facts:
            if f.semantic_kind in (SemanticKind.WRITE,
                                   SemanticKind.STATE_ACCESS):
                src = self._bind_fact(f)
                if not src or src.binding_status is not BindingStatus.EXACT:
                    continue
                sid = self._add_node_from_binding(src, f)
                if not sid:
                    continue
                for o in f.outputs or [f.metadata.get("data")]:
                    if o:
                        writes.setdefault(str(o), set()).add(sid)
        for data, fns in writes.items():
            fns = sorted(fns)
            for i in range(len(fns)):
                for j in range(i + 1, len(fns)):
                    self.edges.append(TopologyEdge(
                        HardEdgeKind.STATE, fns[i], fns[j], TruthLayer.T1,
                        TruthClass.OBSERVED, "MAY", "EXACT", "COMPLETE",
                        witnesses=[{"kind": "shared_state",
                                    "expr": f"same data '{data}'"}],
                        data=data,
                        source_span=None))

    @staticmethod
    def _loc_dict(loc) -> Optional[dict]:
        if loc is None:
            return None
        if isinstance(loc, dict):
            return loc
        return loc.to_dict()

    def _witness(self, f: AnalysisFact) -> dict:
        return {
            "fact_id": f.fact_id,
            "provider": f.provider,
            "kind": getattr(f.fact_kind, "value", f.fact_kind),
            "semantic": getattr(f.semantic_kind, "value", f.semantic_kind),
            "source": self._loc_dict(f.source_location),
            "expr": f.metadata.get("binding") or f.metadata.get("callee")
            or f.metadata.get("call_name") or f.metadata.get("from_code")
            or "",
        }

    # -- projections (Phase 4/5) ---------------------------------------------
    def project_to_files(self) -> dict[str, Any]:
        """π_file: cross-file edges survive (T6); intra-file stats kept."""
        nodes = self.nodes.values()
        file_of = {n.canonical_symbol_id: n.file for n in nodes}
        cross = []
        intra = 0
        for e in self.edges:
            if e.kind in (HardEdgeKind.CALL, HardEdgeKind.DATA,
                          HardEdgeKind.STATE):
                fs = file_of.get(e.source)
                ft = file_of.get(e.target)
                if fs is not None and ft is not None and fs != ft:
                    cross.append(e.to_dict())
                elif fs is not None and ft is not None:
                    intra += 1
            else:
                cross.append(e.to_dict())
        files = {}
        for n in nodes:
            files.setdefault(n.file, {"functions": [], "resources": set()})
            files[n.file]["functions"].append(n.name)
            files[n.file]["resources"].update(n.resources)
        return {
            "files": {f: {"functions": sorted(v["functions"]),
                          "resources": sorted(v["resources"])}
                      for f, v in files.items()},
            "cross_file_edges": cross,
            "intra_file_edge_count": intra,
        }

    def project_to_modules(self, membership: dict[str, str]) -> dict[str, Any]:
        """Declared module/package membership only (Phase 5, no clustering)."""
        nodes = self.nodes.values()
        module_of = {}
        for n in nodes:
            mod = membership.get(n.file)
            module_of.setdefault(mod or "(unassigned)",
                                 {"functions": [], "edges": []})
            module_of[mod or "(unassigned)"]["functions"].append(n.name)
        for e in self.edges:
            if e.kind in (HardEdgeKind.CALL, HardEdgeKind.DATA,
                          HardEdgeKind.STATE):
                ms = module_of_key(self, e.source, membership, nodes)
                mt = module_of_key(self, e.target, membership, nodes)
                if ms is not None and mt is not None and ms != mt:
                    module_of.setdefault(ms, {"functions": [], "edges": []})
                    module_of.setdefault(mt, {"functions": [], "edges": []})
                    module_of[ms]["edges"].append(
                        {"target_module": mt, "kind": e.kind.value,
                         "witnesses": e.witnesses[:3]})
        return {"modules": {m: {"functions": sorted(v["functions"]),
                                "cross_module_edges": v["edges"]}
                            for m, v in module_of.items()}}


def module_of_key(engine, node_id: str, membership: dict, nodes) -> Optional[str]:
    for n in nodes:
        if n.canonical_symbol_id == node_id:
            return membership.get(n.file)
    return None
