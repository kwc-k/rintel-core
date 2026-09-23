"""Port evidence for DRC (SOFTWARE-DRC0 §1 "Ports").

Ports are NOT re-derived by re-analysis: they come from (a) explicit port
descriptions (FLOW0 SoftwareNetlist / fixtures), (b) frozen facts with
provider-native semantic evidence (joern DATA_FLOW METHOD_PARAMETER_IN
reads, lfortran intent metadata).  Every port keeps its evidence strength
so rules can distinguish "declared/observed" from "inferred" (D3/U003).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Port:
    name: str
    direction: str                      # "input" | "output" | "inout"
    type_: str | None = None            # provider/native semantic type; None = unknown
    evidence: str = "DECLARED"          # DECLARED | OBSERVED | INFERRED
    source_filenames: list[str] = field(default_factory=list)


@dataclass
class PortIndex:
    # canonical symbol id -> {input/output port name -> Port}
    by_fn: dict[str, dict[str, dict[str, Port]]] = field(default_factory=dict)

    def inputs(self, cid: str) -> list[Port]:
        return list(self.by_fn.get(cid, {}).get("input", {}).values())

    def outputs(self, cid: str) -> list[Port]:
        return list(self.by_fn.get(cid, {}).get("output", {}).values())

    def all(self, cid: str) -> list[Port]:
        ports = []
        for d in self.by_fn.get(cid, {}).values():
            ports.extend(d.values())
        return ports

    def add(self, cid: str, port: Port) -> None:
        self.by_fn.setdefault(cid, {}).setdefault(port.direction, {})[port.name] = port

    def resolve(self, cid: str, name: str, direction: str | None = None) -> Port | None:
        for d in self.by_fn.get(cid, {}).values():
            if name in d:
                return d[name]
        return None


def build_port_index(topology_nodes: list[dict]) -> PortIndex:
    """JPL/FAC lanes carry NO port information in the frozen topology;
    this returns an empty index.  Explicit ports come from netlists or
    fixtures; dataflow-derived ports come from `ports_from_dataflows`.
    """
    return PortIndex()


def ports_from_dataflows(node_ids: set[str], dataflows: list[dict]) -> PortIndex:
    """Derive input ports from frozen joern DATA_FLOW facts:
    `from_node == METHOD_PARAMETER_IN` reads are evidence a parameter is
    consumed.  These facts are INFERRED/PARTIAL upstream, so every port
    gets evidence=INFERRED — hard rules may not trust them (D3/U003)."""
    idx = PortIndex()
    for f in dataflows:
        if f.get("fact_kind") != "DATA_FLOW":
            continue
        meta = f.get("metadata") or {}
        from_node = meta.get("from_node")
        code = meta.get("from_code")
        subject = f.get("subject")
        if not code or not subject or subject not in node_ids:
            continue
        if from_node == "METHOD_PARAMETER_IN":
            port = Port(name=code, direction="input", type_=None,
                        evidence="INFERRED")
            existing = idx.by_fn.get(subject, {}).get("input", {}).get(code)
            # keep first (deterministic: facts sorted by fact_id by caller)
            if existing is None:
                idx.add(subject, port)
    return idx
